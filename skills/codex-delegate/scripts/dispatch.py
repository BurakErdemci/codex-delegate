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
2 bad arguments · 3 toolchain too old (codex-cli or Python) · 4 unregistered
MCP server · 5 turn completed but one or more approvals were declined. 5 exists because a blocked
worker used to count as a successful turn: two lanes exited 0 with a
contract-perfect FINAL.txt whose body said "status: blocked ...
permissions were declined", and the only thing that caught it was a human
reading `status:` - the return code must carry it.

--done-file writes that same verdict plus the exit code to a marker file when
the turn ends, whatever way it ends. Detached lanes have no other completion
signal, and the poll is one file test instead of a per-lane watcher loop.

Requires Python 3.11+ (tomllib) and codex-cli 0.145+ (see PERMISSION_SCHEMA_MIN).
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    # This gate runs BEFORE `import tomllib`, and writes the completion marker
    # itself, because both facts are load-bearing. Measured: an interpreter can
    # answer the `PY_OK` handshake the skills use and still lack tomllib (stock
    # macOS /usr/bin/python3 is 3.9.6), so a lane can be dispatched with it.
    # The import then fails at module level - before argparse, before main() -
    # and a detached lane leaves no marker at all, so the documented poll waits
    # forever on a process that died in the first millisecond.
    import os

    _argv = sys.argv[1:]
    _done = None
    for _i, _item in enumerate(_argv):
        _name, _, _inline = _item.partition("=")
        if _name.startswith("--") and len(_name) > 2 and "--done-file".startswith(_name):
            _done = _inline or (_argv[_i + 1] if _i + 1 < len(_argv)
                                and not _argv[_i + 1].startswith("-") else None)
            break
    print(
        f"ERROR: dispatch.py needs Python 3.11+ for tomllib; this is "
        f"{sys.version.split()[0]} at {sys.executable}. Being on PATH and "
        f"answering a handshake is not the same as being able to run this - "
        f"try python3.13/3.12/3.11, or `brew install python`.",
        file=sys.stderr,
    )
    if _done:
        try:
            os.makedirs(os.path.dirname(_done) or ".", exist_ok=True)
            with open(_done, "w", encoding="utf-8") as _fh:
                _fh.write("rc=3\nverdict=NO-TURN (interpreter too old)\nfinal=-\n")
        except OSError:
            pass
    raise SystemExit(3)

import argparse
import json
import os
import shutil
import signal
import subprocess
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
# safe: measured on Windows 11 - 12 declines across two running lanes, the first two declined commands being plain in-lane file reads
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
    # PowerShell -Command strings arrive with their inner quotes escaped, so a
    # quoted word shows up as '\"import' - the leading backslash then reads as
    # a rooted path below. Unescape first so the wrap-strip can do its job.
    # Measured: \"import, \"from and a \"-prefixed regex
    # alternation were all declined "absolute path outside lane", which blocked
    # every Python probe of the run.
    token = token.replace('\\"', '"').replace("\\'", "'")
    token = token.strip(_TOKEN_WRAP)
    if not token:
        return None
    # A bare separator is an operator, not a path operand: Python's
    # `ROOT / 'x'` Path division put a lone '/' in the token stream and it was
    # declined as the filesystem root (same run as above).
    if token.strip("/\\") == "":
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


def enrich_file_change(params: dict, items_by_id: dict) -> dict:
    """Graft the cached fileChange item onto a path-less approval payload.

    0.145's fileChange approval names only the itemId - the changed paths
    travelled EARLIER, in the item/started notification. measured
    (audit-k1c line 1509 vs 1511): an in-lane findings write arrived as
    {"itemId": ..., "reason": null, "grantRoot": null} and was declined
    "unrecognized approval payload" while its full path list sat one
    notification up in the same log. Every findings/probes write in 9 field
    rounds died this way. If no cached item exists the payload is returned
    untouched and the conservative decline stands.
    """
    if _collect_paths(params):
        return params
    item = items_by_id.get(str(params.get("itemId") or ""))
    if isinstance(item, dict):
        return {**params, "item": item}
    return params


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
        # measured: 8/8 rounds BLOCKED-BY-APPROVALS, 74 declines, 0
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
    installed. Measured on Windows 11:
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
    Popen.kill() only reaches the cmd.exe root. Measured twice:
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
                # cp125x on a non-UTF-8 console - and the first non-ASCII byte in
                # a worker message killed the parent with UnicodeDecodeError
                # while the worker ran on (measured: 3 lanes lost).
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
        # fileChange approvals reference paths by itemId only; the items
        # themselves stream past earlier as notifications (see
        # enrich_file_change). One turn holds a handful of these.
        self.file_changes: dict[str, dict] = {}
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
                if method == "item/fileChange/requestApproval":
                    params = enrich_file_change(params, self.file_changes)
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
            # The wire verb for yes is "accept", not "approve". codex 0.146
            # stderr: 'unknown variant `approved`,
            # expected one of `accept`, `acceptForSession`,
            # `acceptWithExecpolicyAmendment`, `applyNetworkPolicyAmendment`,
            # `decline`, `cancel`'. A malformed decision does not fall back to
            # asking again - the router logs "approval request failed" and the
            # tool call dies, so the log shows an approval that never took
            # effect (the first smoke turn logged 3 approves and wrote
            # nothing). "decline" needs no mapping - it was always valid,
            # which is why declining ever worked.
            wire = "accept" if decision == "approve" else "decline"
            self.send({"id": rid, "result": {"decision": wire}})
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
        # Capture fileChange items on the way past - their approval request
        # arrives later carrying only the itemId (see enrich_file_change).
        if method in ("item/started", "item/updated"):
            item = params.get("item") or {}
            if item.get("type") == "fileChange" and item.get("id"):
                self.file_changes[str(item["id"])] = item
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


def write_done(path: Path, rc: int, final_path: Path, verdict: str | None = None) -> None:
    """Drop the completion marker a waiting architect polls for.

    Written on EVERY exit path, crashes included. The failure this closes:
    lanes are launched detached (`nohup ... &`, `Start-Process`) and nothing
    told the architect when one landed, so every run grew a hand-built watcher
    loop per lane - measured at six lanes, six loops, all of them
    grepping for different strings. A marker that appeared only on success
    would be worse than none: the runs that hang are exactly the ones that
    need attention, and a poll that never returns hides them.
    """
    if verdict is not None:
        # An explicit verdict means the caller KNOWS no turn ran, so FINAL.txt
        # must not be consulted: on a rejected argument the file still holds
        # the previous round's report, and reading it announced `rc=2
        # verdict=OK` - a failed launch wearing the last success's clothes.
        lines: list[str] = []
    else:
        verdict = "UNKNOWN"
        try:
            lines = final_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            # No FINAL.txt at all means we died before the turn - an old CLI,
            # an unregistered MCP server, an unreadable prompt. rc says which.
            lines = []
            verdict = "NO-TURN"
    for line in reversed(lines):
        if line.startswith("--- dispatch:"):
            verdict = line.strip("- ").split("dispatch:", 1)[1].strip()
            break
    body = f"rc={rc}\nverdict={verdict}\nfinal={final_path}\n"
    if not path.name:
        # `--done-file ""` reaches here as Path('.'), and `.with_name()` then
        # raises ValueError rather than OSError - straight past the caller's
        # guard, which turned a completed turn into a traceback with no marker.
        # An unset "$D" in a hand-shortened dispatch line is all it takes.
        raise OSError(f"{str(path)!r} is not a file path for a completion marker")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    # os.replace is atomic on POSIX and on NTFS alike. A poller that catches a
    # half-written marker reads rc= as empty and calls a running lane finished,
    # which is the same class of silent-wrong-answer as every other bug in
    # this file's history.
    os.replace(tmp, path)


def build_parser() -> argparse.ArgumentParser:
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
    ap.add_argument("--done-file", type=Path, default=None, metavar="PATH",
                    help="write a completion marker here when the turn ends, however it ends "
                         "(rc=, verdict=, final=). Poll for this one file instead of building "
                         "a watcher per detached lane.")
    return ap


def run(args: argparse.Namespace) -> int:
    # First thing, before any preflight can return: last round's report must
    # not survive into this one. Measured in review - a rejected argument
    # returned 2 while the previous turn's FINAL.txt still sat on disk, so the
    # DONE marker read it back and announced `rc=2 verdict=OK`.
    # Renamed rather than deleted: a turn that never starts should not destroy
    # the evidence of the turn that did, and `close` archives both.
    stale = args.task_dir / "FINAL.txt"
    if stale.is_file():
        os.replace(stale, args.task_dir / "FINAL.prev.txt")

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

    try:
        version = codex_version()
    except (DispatchError, OSError, subprocess.SubprocessError) as exc:
        # A traceback here exits 1 with no FINAL.txt, which the exit-code table
        # promises carries `DISPATCH FAILED`. One classified line instead.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
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
    try:
        prompt = args.prompt_file.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot read --prompt-file {args.prompt_file}: {exc}", file=sys.stderr)
        return 1

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
    # blocked lanes exited 0 with a contract-perfect FINAL.txt
    # and nothing but a human reading `status:` caught it.
    final_path.write_text(final_text.rstrip("\n") + "\n\n" + verdict_line + "\n", encoding="utf-8")
    print(verdict_line)
    print(f"final message -> {final_path}  transcript -> {log_path}")
    return 5 if declines else 0


def peek_flag(argv: list[str], flag: str) -> str | None:
    """Read one flag straight from argv, before argparse gets a say.

    Needed because argparse exits the process itself on a bad flag, and the
    marker has to be written for that too: measured in review, `--timeoutt 10`
    exited 2 with no marker at all, and the documented `while [ ! -f DONE ]`
    poll then waits forever on a lane that will never exist. A typo in a
    hand-edited dispatch line is the likeliest way to reach it.

    Two things it has to match argparse on, both measured: argparse accepts
    unambiguous ABBREVIATIONS (`--done /path` works), and a missing value
    leaves the next flag sitting where the value should be - taken literally,
    `--done-file --task-dir /x` wrote a file named `--task-dir` into the cwd.
    """
    for i, item in enumerate(argv):
        name, _, inline = item.partition("=")
        if not (name.startswith("--") and len(name) > 2 and flag.startswith(name)):
            continue
        if inline:
            return inline or None
        value = argv[i + 1] if i + 1 < len(argv) else None
        # A value that is itself a flag means the value was omitted.
        return None if value is None or value.startswith("-") else value
    return None


def main() -> int:
    argv = sys.argv[1:]
    try:
        args = build_parser().parse_args()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        done = peek_flag(argv, "--done-file")
        if done and code != 0:                      # --help exits 0 and is not a run
            task = peek_flag(argv, "--task-dir") or "."
            write_done(Path(done), code, Path(task) / "FINAL.txt", verdict="NO-TURN")
        raise
    if args.done_file:
        # A marker left by an earlier round is a waiter's false green: the poll
        # returns instantly and the architect reads last round's FINAL.txt as
        # this round's result. Same trap FINAL.txt itself is renamed for.
        # This unlink happens ~100ms into startup, which is not soon enough on
        # its own - a poll launched right after `nohup ... &` can win that race
        # - so `new-lane.py turn` clears it when it seeds the turn as well.
        try:
            args.done_file.unlink(missing_ok=True)
        except OSError:
            pass                                # write_done reports it properly
        # A killed lane must release its waiter too. Measured: SIGTERM left no
        # marker and the documented poll hung forever, with the app-server
        # orphaned on top. SystemExit unwinds the stack, so the `finally` here
        # and the one that closes the server both still run.
        for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):
            sig = getattr(signal, name, None)      # SIGHUP is POSIX, SIGBREAK Windows
            if sig is not None:
                signal.signal(sig, lambda signum, _frame: sys.exit(128 + signum))
    rc = 1
    try:
        rc = run(args)
        return rc
    except SystemExit as exc:
        # A signal handler raises SystemExit(128+signum); without this the
        # marker would say rc=1 and a poller could not tell "killed" from
        # "failed".
        rc = exc.code if isinstance(exc.code, int) else 1
        raise
    finally:
        # finally, not a tail call: an unhandled exception must still release
        # anyone polling the marker, carrying rc=1 rather than silence.
        if args.done_file:
            try:
                write_done(args.done_file, rc, args.task_dir / "FINAL.txt")
            except (OSError, ValueError) as exc:
                # Losing the marker is bad; losing the marker AND the run's own
                # verdict behind a traceback is worse, and that is what an
                # unwritable or malformed done-file path used to do. stderr
                # alone is not enough either - a detached run may discard it -
                # so a fallback marker goes next to the report, where the task
                # dir is known to be writable because FINAL.txt lives there.
                print(f"WARNING: could not write the completion marker "
                      f"({args.done_file}): {exc}. The turn's exit code is {rc}.",
                      file=sys.stderr)
                try:
                    write_done(args.task_dir / "DONE", rc, args.task_dir / "FINAL.txt")
                    print(f"wrote it to {args.task_dir / 'DONE'} instead", file=sys.stderr)
                except (OSError, ValueError):
                    pass


if __name__ == "__main__":
    sys.exit(main())
