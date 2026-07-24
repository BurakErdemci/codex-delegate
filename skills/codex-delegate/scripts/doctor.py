#!/usr/bin/env python3
"""Set up and verify the isolated Codex worker used by the codex-delegate skill.

Three jobs:
  --init          create the worker CODEX_HOME and keep its login in sync
  --list-mcp      show which MCP servers Claude has, and which can be handed over
  --add-mcp NAME  register one of them in the worker home
  --check         fast preflight (structure + login freshness), no model tokens
  --smoke         one tiny real turn, proving the login and protocol actually work

The worker deliberately gets its own CODEX_HOME. The point is not secrecy, it is
that the worker should hold no tool it does not need: a default ~/.codex usually
carries MCP servers with credentials for outside services, and a worker whose
only job is to edit a working tree has no business being able to reach them.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

MAIN_HOME = Path.home() / ".codex"
WORKER_HOME = Path.home() / ".codex-worker"

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

def auth_identity(home: Path) -> tuple[str | None, str | None]:
    try:
        data = json.loads((home / "auth.json").read_text())
    except Exception:
        return None, None
    return (data.get("tokens") or {}).get("account_id"), data.get("last_refresh")


def sync_auth(worker: Path, main: Path) -> str:
    """Point the worker at the main login.

    `codex login` only ever writes the main home. A worker home with its own
    copy silently keeps serving a revoked account after the user re-logs in,
    which looks exactly like a broken token. A symlink removes the second copy
    so the two cannot diverge.
    """
    src, dst = main / "auth.json", worker / "auth.json"
    if not src.exists():
        return "no login in main home — run `codex login` first"
    if dst.is_symlink() and dst.resolve() == src.resolve():
        return "already linked"
    if dst.exists() or dst.is_symlink():
        backup = dst.with_suffix(f".json.bak-{int(time.time())}")
        shutil.move(str(dst), str(backup))
    try:
        dst.symlink_to(src)
        return "linked to main home"
    except OSError:
        shutil.copy2(src, dst)
        dst.chmod(0o600)
        return "copied (symlink unavailable on this filesystem)"


# ── MCP discovery ───────────────────────────────────────────────────────────

def claude_mcp_servers(project: Path | None) -> dict[str, dict[str, Any]]:
    """MCP servers visible to Claude Code: project .mcp.json plus ~/.claude.json."""
    found: dict[str, dict[str, Any]] = {}
    if project:
        try:
            found.update(json.loads((project / ".mcp.json").read_text()).get("mcpServers", {}))
        except Exception:
            pass
    try:
        blob = json.loads((Path.home() / ".claude.json").read_text())
    except Exception:
        return found

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "mcpServers" and isinstance(value, dict):
                    for name, cfg in value.items():
                        found.setdefault(name, cfg)
                else:
                    walk(value)
    walk(blob)
    return found


# Handing the worker a Codex server would let it spawn workers of its own,
# recursively and unbudgeted. There is no task that needs this.
SELF_SERVERS = {"codex", "codex-delegate"}


def reaches_outside(name: str, cfg: dict[str, Any]) -> str | None:
    """Return a reason if this server should not be handed to the worker."""
    if name.lower() in SELF_SERVERS:
        return "is a Codex server — would let the worker delegate recursively"
    url = cfg.get("url") or ""
    if url and not any(h in url for h in ("localhost", "127.0.0.1", "::1")):
        return f"remote endpoint {url}"
    env = cfg.get("env") or {}
    secrets = [k for k in env if k.endswith(("_KEY", "_TOKEN", "_SECRET", "_PAT"))]
    if secrets:
        return f"carries credentials ({', '.join(secrets)})"
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


def worker_servers(home: Path) -> set[str]:
    try:
        with open(home / "config.toml", "rb") as fh:
            return set(tomllib.load(fh).get("mcp_servers", {}))
    except Exception:
        return set()


# ── commands ────────────────────────────────────────────────────────────────

def cmd_init(home: Path) -> int:
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.toml"
    if config.exists():
        say(OK, f"{config} already exists, left untouched")
    else:
        config.write_text(BASE_CONFIG)
        say(OK, f"wrote {config}")
    say(OK, f"auth: {sync_auth(home, MAIN_HOME)}")
    return 0


def cmd_list(home: Path, project: Path | None) -> int:
    servers = claude_mcp_servers(project)
    if not servers:
        say(WARN, "no MCP servers found for Claude Code")
        return 0
    installed = worker_servers(home)
    print(f"\n{'server':24} {'status':12} note")
    print("-" * 78)
    for name, cfg in sorted(servers.items()):
        reason = reaches_outside(name, cfg)
        status = "installed" if name in installed else ("blocked" if reason else "local")
        print(f"{name:24} {status:12} {reason or 'local only — safe to hand over'}")
    print("\nHand one over with:  doctor.py --add-mcp <name>")
    print("Servers marked 'blocked' need --force, and the skill requires you to")
    print("approve any outward-facing server per task, in chat, before dispatch.\n")
    return 0


def cmd_add(home: Path, project: Path | None, name: str, force: bool) -> int:
    servers = claude_mcp_servers(project)
    if name not in servers:
        say(BAD, f"'{name}' is not among Claude's MCP servers: {', '.join(sorted(servers)) or 'none'}")
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
        say(BAD, f"{config} missing — run --init first")
        return 2
    with open(config, "a") as fh:
        fh.write(render_server(name, servers[name]))
    say(OK, f"registered '{name}' in {config}")
    return 0


def cmd_check(home: Path) -> int:
    problems = 0
    if not shutil.which("codex"):
        say(BAD, "codex CLI not on PATH")
        return 1
    version = subprocess.run(["codex", "--version"], capture_output=True, text=True).stdout.strip()
    say(OK, f"codex CLI: {version}")

    if not (home / "config.toml").exists():
        say(BAD, f"{home}/config.toml missing — run --init")
        problems += 1
    else:
        registered = worker_servers(home)
        say(OK, f"worker MCP servers: {', '.join(sorted(registered)) or 'none'}")

    main_id, main_at = auth_identity(MAIN_HOME)
    work_id, work_at = auth_identity(home)
    if not work_id:
        say(BAD, f"no usable login in {home} — run --init")
        problems += 1
    elif work_id != main_id:
        say(BAD, f"worker login is a different account than {MAIN_HOME} "
                 f"({work_id} vs {main_id}); run --init to relink")
        problems += 1
    else:
        say(OK, f"login matches main home (refreshed {work_at})")
    return 1 if problems else 0


def cmd_smoke(home: Path) -> int:
    """Prove the login and protocol work, without trusting structure alone."""
    script = Path(__file__).with_name("dispatch.py")
    with_tmp = Path("/tmp/codex-delegate-smoke")
    with_tmp.mkdir(parents=True, exist_ok=True)
    prompt = with_tmp / "PROMPT.txt"
    prompt.write_text("Reply with exactly: SMOKE_OK\n")
    result = subprocess.run(
        [sys.executable, str(script), "--task-dir", str(with_tmp), "--repo", str(with_tmp),
         "--prompt-file", str(prompt), "--codex-home", str(home), "--effort", "low"],
        capture_output=True, text=True, timeout=300,
    )
    final = (with_tmp / "FINAL.txt").read_text().strip() if (with_tmp / "FINAL.txt").exists() else ""
    if result.returncode == 0 and "SMOKE_OK" in final:
        say(OK, "dispatch round-trip works")
        return 0
    say(BAD, f"smoke test failed (exit {result.returncode}): {result.stderr.strip() or final}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Set up and verify the codex-delegate worker.")
    ap.add_argument("--codex-home", default=WORKER_HOME, type=Path)
    ap.add_argument("--project", default=Path.cwd(), type=Path,
                    help="repository whose .mcp.json should be read (default: cwd)")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--list-mcp", action="store_true")
    ap.add_argument("--add-mcp", metavar="NAME")
    ap.add_argument("--force", action="store_true", help="allow handing over an outward-facing server")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()

    if not any([args.init, args.list_mcp, args.add_mcp, args.check, args.smoke]):
        ap.print_help()
        return 0

    rc = 0
    if args.init:
        rc |= cmd_init(args.codex_home)
    if args.list_mcp:
        rc |= cmd_list(args.codex_home, args.project)
    if args.add_mcp:
        rc |= cmd_add(args.codex_home, args.project, args.add_mcp, args.force)
    if args.check:
        rc |= cmd_check(args.codex_home)
    if args.smoke:
        rc |= cmd_smoke(args.codex_home)
    return rc


if __name__ == "__main__":
    sys.exit(main())
