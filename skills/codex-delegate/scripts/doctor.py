#!/usr/bin/env python3
"""Set up and verify the isolated Codex worker used by the codex-delegate skill.

Commands:
  --init            create the worker CODEX_HOME and keep its login in sync
  --check           fast preflight: structure, login, config parse, CLI version
  --smoke           one tiny real turn - the only proof login+model+protocol work
  --trust PATH      mark a directory trusted in the worker config (lanes need this)
  --untrust PATH    drop that entry again (lane closeout)
  --list-mcp        show which MCP servers Claude has, and what handing one over means
  --add-mcp NAME    register one of them in the worker home
  --remove-mcp NAME unregister one

The worker deliberately gets its own CODEX_HOME. The point is not secrecy, it is
that the worker should hold no tool it does not need: a default ~/.codex usually
carries MCP servers with credentials for outside services, and a worker whose
only job is to edit a working tree has no business being able to reach them.
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 11):  # tomllib arrived in 3.11; stock macOS python3 is 3.9
    sys.exit(
        f"codex-delegate needs Python 3.11+ (this is {sys.version.split()[0]} at {sys.executable}).\n"
        "macOS ships 3.9 at /usr/bin/python3. Try python3.11/3.12/3.13, or `brew install python`.\n"
        "On Windows `python3` is usually the Microsoft Store stub (exit 9009, no Python at all); "
        "the working interpreter is `python`."
    )

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

# codex's own env var wins: a user running a non-default main home is exactly the
# user whose login we must not miss.
MAIN_HOME = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
WORKER_HOME = Path.home() / ".codex-worker"

# dispatch.py refuses below this; --check must apply the same floor, otherwise
# "run --check before writing the spec" is a promise the check cannot keep.
MIN_CODEX = (0, 145, 0)

BASE_CONFIG = """\
# Isolated Codex worker home, managed by the codex-delegate skill.
# Deliberately minimal: no plugins, and no MCP servers beyond the ones you
# hand over explicitly with `doctor.py --add-mcp`.
model = "gpt-5.6-sol"
model_reasoning_effort = "high"
sandbox_mode = "workspace-write"

[sandbox_workspace_write]
network_access = false

[shell_environment_policy]
inherit = "core"
exclude = ["GITHUB_*", "GH_*", "*_TOKEN", "*_API_KEY", "*_SECRET", "PASSWORD"]
"""

OK, WARN, BAD = "  ok  ", " warn ", " FAIL "


def say(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}")


# ── auth ────────────────────────────────────────────────────────────────────

def auth_identity(home: Path) -> tuple[str | None, str | None, str | None]:
    """Return (account_id, last_refresh, kind) - kind is chatgpt|apikey|None."""
    try:
        data = json.loads((home / "auth.json").read_text())
    except Exception:
        return None, None, None
    tokens = data.get("tokens") or {}
    if tokens.get("account_id"):
        return tokens["account_id"], data.get("last_refresh"), "chatgpt"
    if data.get("OPENAI_API_KEY"):
        return None, data.get("last_refresh"), "apikey"
    return None, None, None


def sync_auth(worker: Path, main: Path) -> tuple[bool, str]:
    """Point the worker at the main login.

    `codex login` only ever writes the main home. A worker home with its own
    copy silently keeps serving a revoked account after the user re-logs in,
    which looks exactly like a broken token. A symlink removes the second copy
    so the two cannot diverge.
    """
    src, dst = main / "auth.json", worker / "auth.json"
    if not src.exists():
        return False, f"no login in {main} - run `codex login` first, then re-run --init (set CODEX_HOME if your main home is elsewhere)"
    if dst.is_symlink() and dst.resolve() == src.resolve():
        return True, "already linked"
    if dst.exists() or dst.is_symlink():
        backup = dst.with_suffix(f".json.bak-{int(time.time())}")
        shutil.move(str(dst), str(backup))
    try:
        dst.symlink_to(src)
        return True, "linked to main home"
    except OSError:
        shutil.copy2(src, dst)
        dst.chmod(0o600)
        return True, ("copied (symlink unavailable) - WARNING: re-run --init after every "
                      "`codex login`, the two files can now diverge")


# ── worker config ───────────────────────────────────────────────────────────

def load_worker_config(home: Path) -> dict[str, Any]:
    """Parse config.toml or raise - a malformed config must not read as 'empty'."""
    with open(home / "config.toml", "rb") as fh:
        return tomllib.load(fh)


def worker_servers(home: Path) -> set[str]:
    try:
        servers = load_worker_config(home).get("mcp_servers", {})
        return set(servers) if isinstance(servers, dict) else set()
    except FileNotFoundError:
        return set()
    # Anything else propagates: --check reports it, --add-mcp must not append
    # onto a file that no longer parses.


def trust_project(home: Path, path: Path) -> tuple[bool, str]:
    """Mark `path` trusted in the worker config.

    Codex asks for folder trust per exact project path; dispatch.py can only
    answer that request emptily, so an untrusted cwd looks like a hung turn.
    Trust entries are per path - a trusted parent does NOT cover children
    (measured: a field config carried /private/tmp AND /private/tmp/cdtest).
    """
    resolved = str(path.resolve())
    config = home / "config.toml"
    if not config.exists():
        return False, f"{config} missing - run --init first"
    try:
        existing = load_worker_config(home).get("projects", {})
    except Exception as exc:
        return False, f"{config} does not parse: {exc}"
    if existing.get(resolved, {}).get("trust_level") == "trusted":
        return True, f"already trusted: {resolved}"
    # json.dumps, not f'"{resolved}"': a raw Windows path in a TOML basic
    # string turns \U into a unicode escape and the WHOLE config stops
    # parsing - measured 30 Jul 2026, while --trust still said [ ok ].
    with open(config, "a", encoding="utf-8") as fh:
        fh.write(f'\n[projects.{json.dumps(resolved)}]\ntrust_level = "trusted"\n')
    return True, f"trusted: {resolved}"


def untrust_project(home: Path, path: Path) -> None:
    """Drop `path`'s trust block. Smoke dirs are deleted after the run; leaving
    their entries behind accumulates one dead block per --smoke forever."""
    config = home / "config.toml"
    if not config.exists():
        return
    resolved = str(path.resolve())
    # Match every quoting the entry may carry: json.dumps (what trust_project
    # writes), the raw basic string older versions wrote, and the TOML literal
    # form ('...') a hand-repaired Windows config carries.
    headers = tuple(
        f"[projects.{q}]"
        for q in (json.dumps(resolved), f'"{resolved}"', f"'{resolved}'")
    )
    kept: list[str] = []
    dropping = False
    for line in config.read_text(encoding="utf-8").splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("["):
            dropping = stripped.startswith(headers)
        if not dropping:
            kept.append(line)
    config.write_text("".join(kept), encoding="utf-8")


# ── MCP discovery ───────────────────────────────────────────────────────────

def claude_mcp_servers(project: Path | None) -> dict[str, dict[str, Any]]:
    """MCP servers visible to Claude Code.

    Sources, first hit wins: project .mcp.json, ~/.claude.json (all projects),
    plugin caches. Plugin-provided servers live in the plugin's own .mcp.json,
    not in ~/.claude.json - skipping them made `--add-mcp <plugin server>` fail
    with a message that flatly denied the server existed.
    """
    found: dict[str, dict[str, Any]] = {}
    if project:
        try:
            found.update(json.loads((project / ".mcp.json").read_text()).get("mcpServers", {}))
        except Exception:
            pass
    try:
        blob = json.loads((Path.home() / ".claude.json").read_text())

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "mcpServers" and isinstance(value, dict):
                        for name, cfg in value.items():
                            found.setdefault(name, cfg)
                    else:
                        walk(value)
        walk(blob)
    except Exception:
        pass
    for mcp_json in (Path.home() / ".claude" / "plugins" / "cache").glob("*/*/*/.mcp.json"):
        try:
            for name, cfg in json.loads(mcp_json.read_text()).get("mcpServers", {}).items():
                found.setdefault(name, cfg)
        except Exception:
            continue
    return found


# Handing the worker a coding-agent server would let it spawn workers of its
# own, recursively and unbudgeted. Detection is by COMMAND, not by name - users
# register Codex bridges under arbitrary names.
SELF_COMMANDS = {"codex", "claude"}

# A stdio server that reaches the network runs OUTSIDE the Codex sandbox: the
# worker's own network_access=false does not bind a separate MCP process, and
# granted tools are auto-approved. Heuristic by necessity - name/command hints.
NETWORKING_HINTS = (
    "playwright", "puppeteer", "browser", "fetch", "http", "curl", "github",
    "slack", "gmail", "mail", "notion", "linear", "jira", "aws", "gcloud",
    "docker", "kubernetes",
)


def reaches_outside(name: str, cfg: dict[str, Any]) -> str | None:
    """Return a reason this server should not be handed over silently."""
    command = str(cfg.get("command") or "")
    args = " ".join(str(a) for a in (cfg.get("args") or []))
    if Path(command).name in SELF_COMMANDS or name.lower() in {"codex", "codex-delegate"}:
        return "coding-agent server - would let the worker delegate recursively"
    url = cfg.get("url") or ""
    if url and not any(h in url for h in ("localhost", "127.0.0.1", "::1")):
        return f"remote endpoint {url}"
    env = cfg.get("env") or {}
    secrets = [k for k in env if k.endswith(("_KEY", "_TOKEN", "_SECRET", "_PAT"))]
    if secrets:
        return f"carries credentials ({', '.join(secrets)})"
    blob = f"{name} {command} {args}".lower()
    if any(h in blob for h in NETWORKING_HINTS):
        return "stdio server that reaches the network (runs OUTSIDE the Codex sandbox)"
    return None


def toml_value(value: Any) -> str:
    return json.dumps(value)  # JSON scalars/arrays/strings are valid TOML here


def render_server(name: str, cfg: dict[str, Any]) -> str:
    lines = [f"\n[mcp_servers.{name}]"]
    if cfg.get("url"):
        lines.append(f"url = {toml_value(cfg['url'])}")
    if cfg.get("command"):
        lines.append(f"command = {toml_value(cfg['command'])}")
    if cfg.get("args"):
        lines.append(f"args = {toml_value(cfg['args'])}")
    env = cfg.get("env") or {}
    if env:
        lines.append(f"\n[mcp_servers.{name}.env]")
        lines += [f"{k} = {toml_value(v)}" for k, v in env.items()]
    return "\n".join(lines) + "\n"


# ── commands ────────────────────────────────────────────────────────────────

def cmd_init(home: Path) -> int:
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.toml"
    if config.exists():
        say(OK, f"{config} already exists, left untouched")
    else:
        config.write_text(BASE_CONFIG)
        config.chmod(0o600)
        say(OK, f"wrote {config}")
    ok, msg = sync_auth(home, MAIN_HOME)
    say(OK if ok else BAD, f"auth: {msg}")
    return 0 if ok else 1


def cmd_check(home: Path) -> int:
    problems = 0
    codex = shutil.which("codex")
    if not codex:
        say(BAD, "codex CLI not found on PATH - npm i -g @openai/codex")
        return 1
    try:
        # Spawn the resolved path, not the bare name: npm installs the CLI as
        # codex.CMD on Windows and CreateProcess resolves only .exe, so
        # subprocess.run(["codex", ...]) raises FileNotFoundError WinError 2 on
        # a machine where the which() above just succeeded (measured: Windows 11,
        # codex-cli 0.146.0). On POSIX which() returns an absolute path too, so
        # this is one code path, not a platform branch.
        proc = subprocess.run([codex, "--version"], capture_output=True, text=True, timeout=30)
    except Exception as exc:
        say(BAD, f"codex --version failed: {exc}")
        return 1
    raw = proc.stdout.strip()
    if proc.returncode != 0 or not raw:
        say(BAD, f"codex --version exited {proc.returncode} with no usable output")
        return 1
    version: tuple[int, int, int] | None = None
    for token in raw.replace("v", " ").split():
        parts = token.split(".")
        if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
            version = (int(parts[0]), int(parts[1]), int(parts[2]))
            break
    if version and version < MIN_CODEX:
        say(BAD, f"codex {raw} < {'.'.join(map(str, MIN_CODEX))} - dispatch.py will refuse "
                 "(exit 3, the approval schemas changed). Upgrade before writing a spec.")
        problems += 1
    else:
        say(OK, f"codex CLI: {raw}")

    if not (home / "config.toml").exists():
        say(BAD, f"{home}/config.toml missing - run --init")
        problems += 1
    else:
        try:
            cfg = load_worker_config(home)
            servers = sorted(cfg.get("mcp_servers", {}))
            say(OK, f"config parses; model={cfg.get('model')!r} "
                    f"(only --smoke proves your account can run it); "
                    f"mcp: {', '.join(servers) or 'none'}")
            if (cfg.get("sandbox_workspace_write") or {}).get("network_access") is True:
                say(BAD, "worker config has network_access=true PERMANENTLY - the per-task "
                         "-c override is the sanctioned path; edit config.toml back to false")
                problems += 1
        except Exception as exc:
            say(BAD, f"{home}/config.toml does not parse: {exc}")
            problems += 1

    main_id, _, main_kind = auth_identity(MAIN_HOME)
    work_id, work_at, work_kind = auth_identity(home)
    if work_kind == "apikey":
        say(WARN, "worker home authenticates with an API key, not a ChatGPT login - "
                  "the account-desync check does not apply; verify with --smoke")
    elif not work_id:
        say(BAD, f"no usable login in {home} - run --init")
        problems += 1
    elif main_kind == "chatgpt" and work_id != main_id:
        say(BAD, f"worker login is a different account than {MAIN_HOME} "
                 f"({work_id} vs {main_id}); run --init to relink")
        problems += 1
    else:
        say(OK, f"login matches main home (refreshed {work_at})")
    return 1 if problems else 0


def cmd_smoke(home: Path) -> int:
    """Prove the login, model and protocol work, without trusting structure alone."""
    script = Path(__file__).with_name("dispatch.py")
    tmp = Path(tempfile.mkdtemp(prefix="codex-delegate-smoke-"))
    try:
        ok, msg = trust_project(home, tmp)  # fresh dir = untrusted = stalled first turn
        if not ok:
            say(BAD, f"cannot trust smoke dir: {msg}")
            return 1
        prompt = tmp / "PROMPT.txt"
        prompt.write_text("Reply with exactly: SMOKE_OK\n")
        try:
            result = subprocess.run(
                [sys.executable, str(script), "--task-dir", str(tmp), "--repo", str(tmp),
                 "--prompt-file", str(prompt), "--codex-home", str(home), "--effort", "low"],
                capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            # There is no pgrep on Windows, and the leftover there is a cmd.exe
            # shim -> node -> codex.exe chain rather than one process, so the
            # hint has to name a command that exists on the host it prints on.
            leftovers = ("Get-Process codex,node -ErrorAction SilentlyContinue"
                         if os.name == "nt" else "pgrep -fl 'codex app-server'")
            say(BAD, f"smoke test did not finish in 300s. Check `{leftovers}` "
                     "for leftovers, kill them, then re-run --check.")
            return 1
        final_file = tmp / "FINAL.txt"
        final = final_file.read_text().strip() if final_file.exists() else ""
        if result.returncode == 0 and "SMOKE_OK" in final:
            say(OK, "dispatch round-trip works")
            return 0
        say(BAD, f"smoke test failed (exit {result.returncode}): {result.stderr.strip() or final}")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        untrust_project(home, tmp)


def cmd_trust(home: Path, path: Path) -> int:
    ok, msg = trust_project(home, path)
    say(OK if ok else BAD, msg)
    return 0 if ok else 1


def cmd_untrust(home: Path, path: Path) -> int:
    untrust_project(home, path)
    say(OK, f"trust entry removed (if present): {path.resolve()}")
    return 0


def cmd_list(home: Path, project: Path | None) -> int:
    servers = claude_mcp_servers(project)
    if not servers:
        say(WARN, "no MCP servers found for Claude Code")
        return 0
    installed = worker_servers(home)
    print(f"scanning: {project}/.mcp.json, ~/.claude.json (all projects), plugin caches")
    print(f"\n{'server':24} {'status':12} note")
    print("-" * 78)
    for name, cfg in sorted(servers.items()):
        reason = reaches_outside(name, cfg)
        status = "installed" if name in installed else ("blocked" if reason else "local?")
        print(f"{name:24} {status:12} "
              f"{reason or 'no remote url, no credential env - heuristic only, check the server yourself'}")
    print("\nEvery granted server runs OUTSIDE the Codex sandbox and its tool calls are")
    print("auto-approved for the turn. network_access=false does not constrain it.")
    print("Hand one over with:  doctor.py --add-mcp <name>   (blocked ones need --force,")
    print("and outward-facing servers need the user's approval in chat, per task).\n")
    return 0


def cmd_add(home: Path, project: Path | None, name: str, force: bool) -> int:
    servers = claude_mcp_servers(project)
    if name not in servers:
        say(BAD, f"'{name}' not found in {project}/.mcp.json, ~/.claude.json, or plugin caches. "
                 f"Known: {', '.join(sorted(servers)) or 'none'}")
        return 2
    if name in worker_servers(home):
        say(OK, f"'{name}' already registered in the worker home")
        return 0
    reason = reaches_outside(name, servers[name])
    if reason and not force:
        say(BAD, f"'{name}' {reason}.")
        print("      Handing this to an autonomous worker lets it act on the outside world.")
        print("      Re-run with --force if that is genuinely what you want.")
        return 2
    config = home / "config.toml"
    if not config.exists():
        say(BAD, f"{config} missing - run --init first")
        return 2
    try:
        load_worker_config(home)  # never append onto a file that no longer parses
    except Exception as exc:
        say(BAD, f"{config} does not parse: {exc} - fix it before adding servers")
        return 2
    env = servers[name].get("env") or {}
    with open(config, "a") as fh:
        fh.write(render_server(name, servers[name]))
    config.chmod(0o600)  # env values may be real secrets, now in a second file
    if env:
        say(WARN, f"copied env values ({', '.join(env)}) into {config} - "
                  "they now exist in two places; --remove-mcp deletes this copy")
    say(OK, f"registered '{name}' in {config}")
    return 0


def cmd_remove(home: Path, name: str) -> int:
    """Cut one [mcp_servers.NAME] block (and its .env) out of config.toml."""
    config = home / "config.toml"
    if not config.exists():
        say(BAD, f"{config} missing")
        return 2
    if name not in worker_servers(home):
        say(WARN, f"'{name}' is not registered - nothing to remove")
        return 0
    kept: list[str] = []
    dropping = False
    for line in config.read_text().splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("["):
            dropping = stripped.startswith((f"[mcp_servers.{name}]", f"[mcp_servers.{name}."))
        if not dropping:
            kept.append(line)
    config.write_text("".join(kept))
    config.chmod(0o600)
    try:
        remaining = worker_servers(home)
    except Exception as exc:
        say(BAD, f"rewrite left {config} unparseable: {exc} - restore from git or re-run --init")
        return 2
    if name in remaining:
        say(BAD, f"'{name}' still present after rewrite - remove it from {config} by hand")
        return 2
    say(OK, f"removed '{name}' from {config}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--codex-home", default=WORKER_HOME, type=Path,
                    help="worker home to operate on (default: ~/.codex-worker)")
    ap.add_argument("--project", default=Path.cwd(), type=Path,
                    help="repository whose .mcp.json should be read (default: cwd)")
    ap.add_argument("--init", action="store_true", help="create the worker home, link its login")
    ap.add_argument("--check", action="store_true", help="preflight: structure, login, config, CLI version")
    ap.add_argument("--smoke", action="store_true", help="one tiny real turn (spends a little quota)")
    ap.add_argument("--trust", metavar="PATH", type=Path,
                    help="mark PATH trusted in the worker config (every lane worktree needs this)")
    ap.add_argument("--untrust", metavar="PATH", type=Path,
                    help="drop PATH's trust entry (lane closeout - entries accumulate otherwise)")
    ap.add_argument("--list-mcp", action="store_true", help="show Claude's MCP servers and handover status")
    ap.add_argument("--add-mcp", metavar="NAME", help="register a server in the worker home")
    ap.add_argument("--remove-mcp", metavar="NAME", help="unregister a server from the worker home")
    ap.add_argument("--force", action="store_true", help="allow handing over an outward-facing server")
    args = ap.parse_args()

    if not any([args.init, args.list_mcp, args.add_mcp, args.remove_mcp,
                args.check, args.smoke, args.trust, args.untrust]):
        ap.print_help()
        return 0

    rc = 0
    if args.init:
        rc = max(rc, cmd_init(args.codex_home))
    if args.trust:
        rc = max(rc, cmd_trust(args.codex_home, args.trust))
    if args.untrust:
        rc = max(rc, cmd_untrust(args.codex_home, args.untrust))
    if args.list_mcp:
        rc = max(rc, cmd_list(args.codex_home, args.project))
    if args.add_mcp:
        rc = max(rc, cmd_add(args.codex_home, args.project, args.add_mcp, args.force))
    if args.remove_mcp:
        rc = max(rc, cmd_remove(args.codex_home, args.remove_mcp))
    if args.check:
        rc = max(rc, cmd_check(args.codex_home))
    if args.smoke:
        rc = max(rc, cmd_smoke(args.codex_home))
    return rc


if __name__ == "__main__":
    sys.exit(main())
