#!/usr/bin/env python3
"""Run one Codex worker turn through `codex app-server` and report the result.

Why this exists instead of `codex exec`: a one-shot `codex exec` has nowhere to
answer the server->client approval requests Codex raises for MCP tool calls, so
every MCP call dies as "user cancelled MCP tool call". The only `exec` escape is
--dangerously-bypass-approvals-and-sandbox, which removes the sandbox the whole
delegation model depends on. Speaking the app-server protocol lets us grant MCP
permissions while keeping the sandbox intact.

Isolation contract: the streaming transcript (the worker's reasoning, file reads
and tool output) goes to RAW_OUTPUT.log and is never printed to stdout. Only the
worker's final message is written to FINAL.txt. The architect reads FINAL.txt.

Exit codes: 0 turn completed, no approvals declined · 1 dispatch/turn failed ·
2 bad arguments · 3 codex-cli too old · 4 unregistered MCP server · 5 turn
completed but one or more approvals were declined. 5 exists because a blocked
worker used to count as a successful turn: measured 30 Jul 2026, two lanes
exited 0 with a contract-perfect FINAL.txt whose body said "status: blocked ...
permissions were declined", and the only thing that caught it was a human
reading `status:` - the return code must carry it.

Requires Python 3.11+ (tomllib) and codex-cli 0.145+ (see PERMISSION_SCHEMA_MIN).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

# A general `-c key=value` passthrough can switch off the very isolation this
# protocol rests on, so these prefixes are refused. Everything else is allowed:
# the common legitimate use is
#   -c sandbox_workspace_write.network_access=true
# which a research task needs and which still leaves the filesystem sandbox on.
CONFIG_DENYLIST = (
    "sandbox_mode",
    "approval_policy",
    "default_permissions",
    "mcp_servers",
    "shell_environment_policy",
    "trusted_projects",
    "projects",
)

# Below this codex-cli version, item/permissions/requestApproval used the old
# {"decision": ...} reply. We refuse to guess which schema is live.
PERMISSION_SCHEMA_MIN = (0, 145, 0)

DECISION_APPROVALS = (
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
)
PERMISSION_APPROVAL = "item/permissions/requestApproval"


# --- approval decisions ------------------------------------------------------
#
# On POSIX the OS sandbox (seatbelt, measured on macOS) enforces
# workspace-write before Codex ever asks, so an approval request that reaches
# this client really is an attempt to act outside the sandbox and blanket
# decline is correct there. On Windows no OS sandbox runs (the vendor sandbox
# setup exe is not installed) and Codex escalates anything it cannot prove
# safe: measured 30 Jul 2026 on Turkish Windows 11, 12 declines across 2
# running lanes, the first two declined commands being plain in-lane file reads
# (a `powershell -Command '$files = @(...)'` pipeline and a
# `Get-Content -LiteralPath ... | ForEach-Object`). Both lanes finished all
# their reading yet shipped ZERO artifacts and reported "required write and
# probe-run permissions were declined". Path-scoping the decision to the lane
# worktree is the containment substitute while no OS sandbox runs.

_PATH_KEYS = ("path", "filePath", "file_path", "file")
# Wrapper punctuation seen around paths embedded in PowerShell one-liners
# (quoting, arrays, pipelines) - stripped so @('Backend/x.py', is judged as
# the path it carries, not as an opaque token.
_TOKEN_WRAP = "'\"`(),;@"


def _inside_lane(candidate: str, lane_root: Path, cwd: str | None = None) -> bool:
    """Lexical containment: does candidate land inside lane_root?

    normpath+join instead of Path.resolve(): approval payloads name paths that
    do not exist yet (the request precedes the write), and staying lexical
    keeps approval_decision free of filesystem access. normcase because NTFS
    compares case-insensitively. ntpath.join keeps the drive when handed a
    rooted drive-less path, so a POSIX-style /etc is judged on the lane's own
    drive rather than passed through unanchored.
    """
    anchored = os.path.normcase(os.path.normpath(
        os.path.join(cwd or str(lane_root), candidate)))
    root = os.path.normcase(os.path.normpath(str(lane_root)))
    return anchored == root or anchored.startswith(root + os.sep)


def _collect_paths(node: Any) -> list[str]:
    """Every string under a path-like key, any nesting - the payload shape is
    undocumented and has already changed once (0.145), so a fixed shape here
    would silently stop finding paths and approve nothing ever again."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _PATH_KEYS and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_collect_paths(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_collect_paths(value))
    return found


def _token_verdict(token: str, lane_root: Path, cwd: str) -> str | None:
    """Reason to decline this argv token, or None if it is lane-safe."""
    token = token.strip(_TOKEN_WRAP)
    if not token:
        return None
    upper = token.upper()
    if (token == "~" or token.startswith(("~/", "~\\"))
            or "$HOME" in upper or "%USERPROFILE%" in upper
            or "$ENV:USERPROFILE" in upper):
        return f"home reference in {token!r}"
    # Location env vars are absolute paths in disguise: a write to $env:TEMP
    # lands outside the lane while every literal-path check above passes -
    # measured in review, the probe that closed this hole approved
    # `Set-Content $env:TEMP\out.txt`. Named list, not a blanket $env:/%VAR%
    # ban: PATH-style reads are how probes find their own tools.
    for var in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
                "HOMEDRIVE", "HOMEPATH", "PUBLIC"):
        if f"%{var}%" in upper or f"$ENV:{var}" in upper:
            return f"location env var in {token!r}"
    drive_abs = len(token) >= 3 and token[0].isalpha() and token[1] == ":" and token[2] in "\\/"
    if drive_abs or token.startswith(("\\\\", "//", "/", "\\")):
        if not _inside_lane(token, lane_root):
            return f"absolute path outside lane: {token!r}"
    elif ".." in token and not _inside_lane(token, lane_root, cwd):
        return f"path escapes lane: {token!r}"
    return None


def approval_decision(method: str, params: dict, lane_root: Path) -> tuple[str, str]:
    """Windows decision for one sandbox approval: ("approve"|"decline", reason).

    Pure - no I/O, no process state - so the containment boundary is testable
    without a live app-server. These are the WINDOWS semantics only; POSIX
    keeps the blanket decline inline in AppServer.approve(), because there the
    OS sandbox already filtered the request (see the block comment above).
    """
    if method == "item/fileChange/requestApproval":
        paths = _collect_paths(params)
        if not paths:
            # A payload we cannot read is a payload we cannot scope.
            return "decline", "unrecognized approval payload"
        for p in paths:
            if not _inside_lane(p, lane_root):
                return "decline", f"file path outside lane: {p!r}"
        return "approve", f"{len(paths)} file path(s) inside lane"
    if method == "item/commandExecution/requestApproval":
        sources: list[dict] = [params]
        item = params.get("item")
        if isinstance(item, dict):
            sources.append(item)
        command: str | list | None = None
        req_cwd: str | None = None
        for src in sources:
            if command is None:
                for key in ("command", "cmd", "commandLine", "argv"):
                    value = src.get(key)
                    if isinstance(value, (str, list)) and value:
                        command = value
                        break
            if req_cwd is None and isinstance(src.get("cwd"), str):
                req_cwd = src["cwd"]
        if command is None:
            return "decline", "unrecognized approval payload"
        cwd_abs = os.path.normpath(os.path.join(str(lane_root), req_cwd or ""))
        if not _inside_lane(cwd_abs, lane_root):
            return "decline", f"cwd outside lane: {req_cwd!r}"
        # A -Command one-liner arrives as a single argv element; split every
        # element on whitespace so paths embedded inside the script text are
        # judged too, not just the outer argv.
        tokens = (command.split() if isinstance(command, str)
                  else [tok for element in command for tok in str(element).split()])
        # argv[0] is the PROGRAM, not an operand - every interpreter lives
        # outside the lane by definition. Judging it under the containment
        # rule declined every shell-wrapped command Codex issued (measured
        # 31 Jul 2026: 8/8 rounds BLOCKED-BY-APPROVALS, 74 declines, 0
        # approvals) and shadowed the real rule: a genuine escaping write was
        # declined for the WRONG reason. Exempting it loses no containment -
        # the same worker reaches any executable through an approved shell
        # anyway; this lexical scan is a write-containment proxy, not a
        # sandbox.
        for token in tokens[1:]:
            reason = _token_verdict(token, lane_root, cwd_abs)
            if reason:
                return "decline", reason
        return "approve", "command scoped to lane"
    # DECISION_APPROVALS may grow; a method this function does not understand
    # must never be approved by accident.
    return "decline", f"unhandled approval method {method}"


class DispatchError(RuntimeError):
    pass


def codex_path() -> str:
    """Absolute path to the codex CLI - never spawn it by bare name.

    npm installs the CLI as codex.CMD on Windows, and CreateProcess resolves
    only .exe, so the bare name fails on a machine where codex is perfectly well
    installed. Measured (Windows 11, Python 3.13.13, codex-cli 0.146.0):
    subprocess.run(["codex", "--version"]) raises FileNotFoundError WinError 2,
    while the same call through shutil.which("codex") ->
    ...\\AppData\\Roaming\\npm\\codex.CMD returns rc=0. which() returns an
    absolute path on POSIX as well, so this needs no platform branch.
    """
    resolved = shutil.which("codex")
    if resolved is None:
        raise DispatchError("codex CLI not found on PATH - npm i -g @openai/codex")
    return resolved


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the app-server and everything it spawned.

    On Windows the npm shim builds a cmd.exe -> node.exe -> codex.exe chain and
    Popen.kill() only reaches the cmd.exe root. Measured twice on this machine:
    after proc.kill() the node and codex.exe processes were still alive, holding
    the model session open with nobody left to read them. taskkill /T walks the
    tree. POSIX keeps kill(), where there is no shim to hide behind.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True)
    else:
        proc.kill()


def codex_version() -> tuple[int, int, int]:
    out = subprocess.run(
        [codex_path(), "--version"], capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=30
    ).stdout
    for token in out.replace("v", " ").split():
        parts = token.split(".")
        if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
            return (int(parts[0]), int(parts[1]), int(parts[2]))
    raise DispatchError(f"could not parse codex version from: {out!r}")


def configured_mcp_names(codex_home: Path) -> set[str]:
    """MCP server names registered in this home's config.toml.

    Passing approval config for a server that is not registered makes Codex
    reject the entire thread/start, because the synthesised entry has no
    transport field. So we only ever name servers that already exist.
    """
    try:
        with open(codex_home / "config.toml", "rb") as fh:
            servers = tomllib.load(fh).get("mcp_servers", {})
        return set(servers) if isinstance(servers, dict) else set()
    except FileNotFoundError:
        return set()
    except Exception as exc:  # a malformed config must not fail silently
        raise DispatchError(f"cannot read {codex_home}/config.toml: {exc}") from exc


class AppServer:
    def __init__(self, codex_home: Path, cwd: Path, log, effort: str | None,
                 overrides: list[str] | None = None, timeout: int | None = None):
        spawn = [codex_path(), "app-server"]
        if effort:
            spawn += ["-c", f"model_reasoning_effort={effort}"]
        for kv in overrides or []:
            spawn += ["-c", kv]
        env = {**os.environ, "CODEX_HOME": str(codex_home), "NO_COLOR": "1"}
        # Never hand the worker inherited provider credentials; it authenticates
        # through the ChatGPT login stored in codex_home. SSH_AUTH_SOCK is on the
        # list because an agent socket defeats the point of dropping GITHUB_*:
        # a worker that can talk to ssh-agent can push. AWS_ is a prefix match
        # because AWS_ACCESS_KEY_ID ends in neither _KEY nor _TOKEN.
        for key in list(env):
            if (
                key.endswith(("_API_KEY", "_TOKEN", "_SECRET"))
                or key.startswith(("GITHUB_", "GH_", "AWS_"))
                or key in ("SSH_AUTH_SOCK", "GOOGLE_APPLICATION_CREDENTIALS")
            ):
                env.pop(key, None)
        try:
            self.proc = subprocess.Popen(
                # stderr joins the transcript: startup failures (bad config,
                # unknown model, missing auth, MCP server refused to start) are
                # reported ONLY there, and discarding it leaves the architect
                # with "exited unexpectedly" and no cause.
                spawn, cwd=str(cwd), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                # The app-server speaks UTF-8 JSON regardless of console
                # codepage. text=True alone decodes with the locale default -
                # cp125x on Turkish Windows - and the first non-ASCII byte in
                # a worker message killed the parent with UnicodeDecodeError
                # while the worker ran on (measured 30 Jul 2026, 3 lanes lost).
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except FileNotFoundError as exc:
            # spawn[0] came out of shutil.which, so "is codex on PATH?" is the
            # wrong question here - PATH already answered it. Name the path that
            # failed instead; that is what distinguishes a vanished install from
            # an unreadable one.
            raise DispatchError(
                f"cannot start `codex app-server` from {spawn[0]!r} "
                f"({exc})"
            ) from exc
        except OSError as exc:
            raise DispatchError(f"cannot start `codex app-server`: {exc}") from exc
        self.log = log
        self._next_id = 0
        self.final_text = ""
        self.timed_out = False
        # cwd is the lane worktree; approval scoping needs it, and the count of
        # declines is what separates "turn completed" from "turn completed but
        # the worker was starved" (see approval_decision and main()).
        self.lane_root = Path(cwd)
        self.declined = 0
        # read() blocks in readline() with no deadline of its own, so a wall
        # clock check in the caller's loop can never fire while the worker is
        # silent. Kill the process from a timer instead; readline() then returns
        # "" and read() reports the timeout.
        self._timer = None
        if timeout:
            self._timer = threading.Timer(timeout, self._on_timeout)
            self._timer.daemon = True
            self._timer.start()

    def _on_timeout(self) -> None:
        self.timed_out = True
        try:
            kill_tree(self.proc)
        except Exception:
            pass

    def send(self, obj: dict[str, Any]) -> None:
        assert self.proc.stdin
        try:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise DispatchError(f"app-server closed its input pipe: {exc}") from exc

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        rid = self._next_id
        self.send({"id": rid, "method": method, "params": params})
        while True:
            msg = self.read()
            if msg.get("id") == rid and "method" not in msg:
                if msg.get("error"):
                    raise DispatchError(f"{method} failed: {msg['error']}")
                return msg.get("result") or {}
            self.handle(msg)

    def read(self) -> dict[str, Any]:
        assert self.proc.stdout
        line = self.proc.stdout.readline()
        if not line:
            if self.timed_out:
                raise DispatchError("timed out waiting for the worker; app-server was killed")
            raise DispatchError(
                "codex app-server exited unexpectedly - its stderr is interleaved "
                "into this transcript (unprefixed lines); read the tail for the cause"
            )
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            self.log.write(f"[non-json] {line}")
            return {}

    def handle(self, msg: dict[str, Any]) -> None:
        """Answer server->client requests; record stream notifications."""
        method = msg.get("method", "")
        if "id" in msg and method:
            self.approve(msg)
        elif method:
            self.note(method, msg.get("params") or {})

    def approve(self, msg: dict[str, Any]) -> None:
        rid, method = msg["id"], msg["method"]
        params = msg.get("params") or {}
        if method == PERMISSION_APPROVAL:
            # 0.145+ rejects {"decision": ...} here. Grant exactly the profile
            # asked for, scoped to this turn so nothing is persisted.
            self.log.write(f"[approve] mcp permissions {json.dumps(params.get('permissions', {}))}\n")
            self.send({"id": rid, "result": {"permissions": params.get("permissions", {}), "scope": "turn"}})
        elif method in DECISION_APPROVALS:
            if os.name == "nt":
                # No OS sandbox backs the worker here, so these requests are
                # NOT escape attempts - Codex asks for anything it cannot prove
                # safe, and blanket decline starved two lanes to zero artifacts
                # (see the approval-decision block above for the measurement).
                decision, reason = approval_decision(method, params, self.lane_root)
            else:
                # The POSIX sandbox already filtered workspace writes; a
                # request that still arrives is an attempt to act outside it,
                # and declining is the isolation guarantee. Auto-accepting
                # would quietly hand back full machine access.
                decision, reason = "decline", "outside-sandbox request"
            if decision == "approve":
                self.log.write(f"[approve] {method}: {reason}: {json.dumps(params)[:200]}\n")
            else:
                self.declined += 1
                # The "[decline] {method}:" prefix is load-bearing: the audit
                # skill's containment protocol greps RAW_OUTPUT.log for
                # "[decline] item/commandExecution/requestApproval" as proof
                # the harness, not the OS, answered. Do not reword the prefix.
                # The reason rides along because without it the argv[0] bug
                # could not be diagnosed from the log - the function had to be
                # re-run by hand against captured payloads.
                self.log.write(f"[decline] {method}: {reason}: {json.dumps(params)[:400]}\n")
            self.send({"id": rid, "result": {"decision": decision}})
        elif method == "item/tool/requestUserInput":
            # 0.145 expects an answer map keyed by question id:
            #   {"answers": {"<question-id>": {"answers": ["<text>"]}}}
            # The pre-0.145 shape {"value": "..."} is accepted by the transport
            # and then silently ignored, so the tool reports that the user never
            # answered and the worker stalls on a question nobody will answer.
            reply = "Proceed using your best judgment; do not ask for confirmation."
            questions = params.get("questions") or []
            answers = {
                q["id"]: {"answers": [reply]}
                for q in questions
                if isinstance(q, dict) and q.get("id")
            }
            self.log.write(f"[auto-answer] {len(answers)} question(s): {json.dumps(params)[:400]}\n")
            self.send({"id": rid, "result": {"answers": answers}})
        else:
            # Any unanswered server request hangs the session forever - there is
            # no timeout on the Codex side, so a silent skip here freezes the run.
            self.log.write(f"[auto-empty] unhandled server request {method}: {json.dumps(params)[:400]}\n")
            self.send({"id": rid, "result": {}})

    def note(self, method: str, params: dict[str, Any]) -> None:
        if method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage":
                self.final_text = item.get("text", "") or self.final_text
            self.log.write(f"[{item.get('type', 'item')}] {json.dumps(item)[:4000]}\n")
        elif method == "turn/failed":
            raise DispatchError(f"turn failed: {json.dumps(params)[:800]}")
        else:
            self.log.write(f"[{method}] {json.dumps(params)[:1000]}\n")
        self.log.flush()

    def close(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        if os.name == "nt":
            # terminate() is TerminateProcess against the cmd.exe shim alone, so
            # wait() would report a clean exit while node and codex.exe keep
            # running (measured: 2 orphans per run, twice). Kill the tree first,
            # while the parent links still exist - taskkill after the root has
            # gone can only find children by a pid Windows may have recycled.
            kill_tree(self.proc)
            try:
                self.proc.wait(timeout=10)
            except Exception:
                pass
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Dispatch one Codex worker turn.")
    ap.add_argument("--task-dir", required=True, type=Path, help="run directory that holds SPEC.md")
    ap.add_argument("--repo", required=True, type=Path, help="repository root the worker runs in")
    ap.add_argument("--prompt-file", required=True, type=Path, help="file whose contents become the turn input")
    ap.add_argument("--codex-home", default=Path.home() / ".codex-worker", type=Path)
    ap.add_argument("--mcp", action="append", default=[], metavar="NAME",
                    help="grant this MCP server for this task (repeatable)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--effort", default="high")
    ap.add_argument("--sandbox", default="workspace-write",
                    choices=["read-only", "workspace-write"])
    ap.add_argument("--timeout", type=int, default=3600, help="seconds before giving up")
    ap.add_argument("-c", "--config", action="append", default=[], metavar="KEY=VALUE",
                    help="Codex config override, dotted path, repeatable. The one you are "
                         "probably looking for is sandbox_workspace_write.network_access=true, "
                         "which lets the worker's shell reach the network while keeping the "
                         "filesystem sandbox. Keys that would disable the sandbox are refused.")
    args = ap.parse_args()

    for kv in args.config:
        if "=" not in kv:
            print(f"ERROR: --config expects KEY=VALUE, got {kv!r}", file=sys.stderr)
            return 2
        key = kv.split("=", 1)[0].strip()
        if key.split(".")[0] in CONFIG_DENYLIST:
            print(
                f"ERROR: --config {key} is refused. It would weaken or remove the sandbox, "
                "which is the isolation guarantee the whole delegation model rests on. "
                "If the task genuinely needs it, do the work yourself instead.",
                file=sys.stderr,
            )
            return 2
        # A misspelled key is accepted by Codex and silently ignored, so the
        # worker hits the same wall with no explanation. Warn on the near-miss.
        if key.replace("-", "_") != key:
            print(f"WARNING: --config {key} contains '-'; Codex keys use '_' and a wrong key "
                  "is ignored without error.", file=sys.stderr)

    version = codex_version()
    if version < PERMISSION_SCHEMA_MIN:
        print(
            f"ERROR: codex-cli {'.'.join(map(str, version))} is older than "
            f"{'.'.join(map(str, PERMISSION_SCHEMA_MIN))}. The approval and tool-input "
            "reply schemas changed in 0.145 and this script only implements the newer "
            "ones - the gate applies with or without --mcp. Upgrade codex "
            "(npm i -g @openai/codex).",
            file=sys.stderr,
        )
        return 3

    task_dir: Path = args.task_dir
    task_dir.mkdir(parents=True, exist_ok=True)
    prompt = args.prompt_file.read_text(encoding="utf-8")

    granted: list[str] = []
    if args.mcp:
        available = configured_mcp_names(args.codex_home)
        missing = [n for n in args.mcp if n not in available]
        if missing:
            print(
                f"ERROR: MCP server(s) {missing} are not registered in {args.codex_home}/config.toml. "
                "Naming an absent server makes Codex reject thread/start outright. "
                "Run doctor.py --add-mcp <name> first.",
                file=sys.stderr,
            )
            return 4
        granted = list(args.mcp)

    log_path = task_dir / "RAW_OUTPUT.log"
    final_path = task_dir / "FINAL.txt"
    # A report left by an earlier round must never be mistaken for this one's.
    # Without this, a failed retry leaves the previous turn's "completed / pass"
    # on disk and the architect reads it as the result of the round that failed.
    final_path.unlink(missing_ok=True)
    started = time.monotonic()
    server: AppServer | None = None

    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n===== dispatch {time.strftime('%Y-%m-%d %H:%M:%S')} "
                  f"mcp={granted or 'none'} sandbox={args.sandbox} "
                  f"config={args.config or 'none'} =====\n")
        log.flush()
        try:
            server = AppServer(args.codex_home, args.repo, log, args.effort,
                               overrides=args.config, timeout=args.timeout)
        except DispatchError as exc:
            log.write(f"[fatal] {exc}\n")
            verdict_line = "--- dispatch: FAILED ---"
            final_path.write_text(f"DISPATCH FAILED: {exc}\n{verdict_line}\n", encoding="utf-8")
            print(verdict_line)
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        try:
            # Keep in step with .claude-plugin/plugin.json "version".
            server.request("initialize", {"clientInfo": {"name": "codex-delegate", "version": "2.1.0"}})
            server.send({"method": "initialized"})

            config: dict[str, Any] = {}
            if granted:
                config["mcp_servers"] = {n: {"default_tools_approval_mode": "approve"} for n in granted}
            params: dict[str, Any] = {
                "cwd": str(args.repo),
                "approvalPolicy": "on-request",
                "sandbox": args.sandbox,
                "config": config,
            }
            if args.model:
                params["model"] = args.model
            thread = server.request("thread/start", params)
            thread_id = (thread.get("thread") or {}).get("id")
            if not thread_id:
                raise DispatchError(f"thread/start returned no thread id: {thread}")

            server.request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]})

            while True:
                if time.monotonic() - started > args.timeout:
                    raise DispatchError(f"timed out after {args.timeout}s")
                msg = server.read()
                if msg.get("method") == "turn/completed":
                    params = msg.get("params") or {}
                    server.note("turn/completed", params)
                    # turn/completed is NOT a success signal - it also carries
                    # refusals and provider-side failures. Measured: OpenAI's
                    # cybersecurity classifier rejected a red-team turn
                    # (status=failed, codexErrorInfo=cyberPolicy) and this
                    # wrapper still printed OK, so an empty lane was delivered
                    # as a finished one.
                    turn = params.get("turn") or params
                    status = turn.get("status") or params.get("status")
                    if status is not None and status != "completed":
                        err = turn.get("error") or params.get("error") or {}
                        kind = err.get("codexErrorInfo") if isinstance(err, dict) else None
                        raise DispatchError(
                            f"turn ended with status={status!r}"
                            + (f" codexErrorInfo={kind!r}" if kind else "")
                            + f": {json.dumps(err)[:400]}"
                        )
                    break
                server.handle(msg)
        except DispatchError as exc:
            log.write(f"[fatal] {exc}\n")
            # Four field failures wore the same clothes (cyberPolicy exit 1,
            # blocked exit 0, mid-turn cyberPolicy exit 1, decode error exit 1)
            # and only opening every log told them apart. One classified line
            # per lane separates them at a glance. The DispatchError text
            # already names codexErrorInfo when the provider refused the turn.
            if server.timed_out:
                verdict = "TIMEOUT"
            elif "cyberPolicy" in str(exc):
                verdict = "REFUSED"
            else:
                verdict = "FAILED"
            verdict_line = f"--- dispatch: {verdict} ---"
            # The architect reads FINAL.txt. If a failed turn leaves nothing
            # there, the previous round's report gets read as this one's.
            final_path.write_text(f"DISPATCH FAILED: {exc}\n{verdict_line}\n", encoding="utf-8")
            print(verdict_line)
            print(f"ERROR: {exc}", file=sys.stderr)
            if server.final_text:
                # A mid-turn cyberPolicy kill arrived AFTER the worker had put
                # its findings in an agentMessage; FINAL.txt got only "DISPATCH
                # FAILED" and three findings were recovered by hand from
                # RAW_OUTPUT.log. Salvage the last message so that never
                # depends on a human rereading the raw transcript.
                salvage_path = task_dir / "SALVAGE.txt"
                salvage_path.write_text(server.final_text.rstrip("\n") + "\n", encoding="utf-8")
                print(f"last agent message salvaged -> {salvage_path}", file=sys.stderr)
            return 1
        finally:
            server.close()

    declines = server.declined
    verdict = (f"BLOCKED-BY-APPROVALS ({declines} approvals declined)"
               if declines else "OK")
    verdict_line = f"--- dispatch: {verdict} ---"
    final_text = server.final_text or "(worker produced no final message)"
    # The verdict rides in FINAL.txt as well as stdout and the exit code: two
    # blocked lanes exited 0 with a contract-perfect FINAL.txt (30 Jul 2026)
    # and nothing but a human reading `status:` caught it.
    final_path.write_text(final_text.rstrip("\n") + "\n\n" + verdict_line + "\n", encoding="utf-8")
    print(verdict_line)
    print(f"final message -> {final_path}  transcript -> {log_path}")
    return 5 if declines else 0


if __name__ == "__main__":
    sys.exit(main())
