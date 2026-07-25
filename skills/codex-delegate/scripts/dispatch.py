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

Requires Python 3.11+ (tomllib) and codex-cli 0.145+ (see PERMISSION_SCHEMA_MIN).
"""

from __future__ import annotations

import argparse
import json
import os
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


class DispatchError(RuntimeError):
    pass


def codex_version() -> tuple[int, int, int]:
    out = subprocess.run(
        ["codex", "--version"], capture_output=True, text=True, timeout=30
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
        spawn = ["codex", "app-server"]
        if effort:
            spawn += ["-c", f"model_reasoning_effort={effort}"]
        for kv in overrides or []:
            spawn += ["-c", kv]
        env = {**os.environ, "CODEX_HOME": str(codex_home), "NO_COLOR": "1"}
        # Never hand the worker inherited provider credentials; it authenticates
        # through the ChatGPT login stored in codex_home.
        for key in list(env):
            if key.endswith(("_API_KEY", "_TOKEN", "_SECRET")) or key.startswith(("GITHUB_", "GH_")):
                env.pop(key, None)
        try:
            self.proc = subprocess.Popen(
                # stderr joins the transcript: startup failures (bad config,
                # unknown model, missing auth, MCP server refused to start) are
                # reported ONLY there, and discarding it leaves the architect
                # with "exited unexpectedly" and no cause.
                spawn, cwd=str(cwd), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                text=True, bufsize=1,
            )
        except FileNotFoundError as exc:
            raise DispatchError(
                "cannot start `codex app-server` - is codex on PATH? "
                f"({exc})"
            ) from exc
        except OSError as exc:
            raise DispatchError(f"cannot start `codex app-server`: {exc}") from exc
        self.log = log
        self._next_id = 0
        self.final_text = ""
        self.timed_out = False
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
            self.proc.kill()
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
            # These fire when the worker tries to act OUTSIDE its sandbox. The
            # sandbox is the isolation guarantee, so declining is the point;
            # auto-accepting here would quietly hand back full machine access.
            self.log.write(f"[decline] {method}: {json.dumps(params)[:400]}\n")
            self.send({"id": rid, "result": {"decision": "decline"}})
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
            f"{'.'.join(map(str, PERMISSION_SCHEMA_MIN))}. The MCP approval reply schema "
            "changed in 0.145 and this script only implements the newer one. "
            "Upgrade codex, or dispatch without --mcp.",
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
            final_path.write_text(f"DISPATCH FAILED: {exc}\n", encoding="utf-8")
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        try:
            server.request("initialize", {"clientInfo": {"name": "codex-delegate", "version": "1.0.0"}})
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
                    server.note("turn/completed", msg.get("params") or {})
                    break
                server.handle(msg)
        except DispatchError as exc:
            log.write(f"[fatal] {exc}\n")
            # The architect reads FINAL.txt. If a failed turn leaves nothing
            # there, the previous round's report gets read as this one's.
            final_path.write_text(f"DISPATCH FAILED: {exc}\n", encoding="utf-8")
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        finally:
            server.close()

    final_text = server.final_text or "(worker produced no final message)"
    final_path.write_text(final_text.rstrip("\n") + "\n", encoding="utf-8")
    print(f"OK: final message -> {final_path}  transcript -> {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
