#!/usr/bin/env python3
"""The [ask] flow end to end: dispatch.run() against fake_app_server.py.

No real Codex: codex_path() is pointed at this interpreter, which then runs a
file named `app-server` in the lane (the cwd AppServer spawns in), and that
file runs fake_app_server.py. Everything else - approval filter, approvals/
files, --decide, the wire replies, the verdict and exit code - is the real
code path.

Run: python test_ask.py    (exit 0 = every case holds)
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISPATCH = HERE / "dispatch.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dsp = _load("dispatch_for_ask", DISPATCH)
with contextlib.redirect_stdout(io.StringIO()):
    FIELD = _load("approvals_for_ask", HERE / "test_approvals.py").FIELD_2026_11_25

CMD = "item/commandExecution/requestApproval"
dsp.codex_path = lambda: sys.executable
dsp.codex_version = lambda: (0, 171, 0)


class Lane:
    """A throwaway lane with a fake app-server scenario."""

    def __init__(self, root: Path, requests: list[dict], **extra):
        self.repo = root / "lane"
        self.task = self.repo / ".delegate-runs" / "t1"
        self.task.mkdir(parents=True)
        self.record = root / "wire.jsonl"
        self.cfg = root / "fake.json"
        self.scenario(requests, **extra)
        (self.repo / "app-server").write_text(
            f"import runpy\nrunpy.run_path({str(HERE / 'fake_app_server.py')!r}, run_name='__main__')\n",
            encoding="utf-8")
        self.prompt = root / "prompt.txt"
        self.prompt.write_text("do the thing", encoding="utf-8")

    def scenario(self, requests: list[dict], **extra) -> None:
        self.record.unlink(missing_ok=True)
        self.record.touch()
        self.cfg.write_text(json.dumps({"record": str(self.record), "requests": requests, **extra}),
                            encoding="utf-8")
        os.environ["FAKE_APP_SERVER_CFG"] = str(self.cfg)

    def ask(self, *argv: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dsp.ask_codex([str(self.task), *argv, "--codex-home", str(self.repo / "home")])
        # A failed --ask keeps its transcript in the temp dir for diagnosis;
        # the test made it, so the test removes it.
        for line in err.getvalue().splitlines():
            if line.startswith("transcript kept at "):
                Path(line[len("transcript kept at "):]).unlink(missing_ok=True)
        return rc, out.getvalue()

    def snapshot(self) -> dict[str, str]:
        import hashlib
        return {str(p.relative_to(self.repo)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(self.repo.rglob("*")) if p.is_file() and p.name != "ASK.log"}

    def results(self) -> list[dict]:
        return [json.loads(line)["in"].get("result")
                for line in self.record.read_text(encoding="utf-8").splitlines()
                if json.loads(line)["in"].get("id", 0) > 1000 and "method" not in json.loads(line)["in"]]

    def run(self, *flags: str) -> tuple[int, str]:
        args = dsp.build_parser().parse_args([
            "--task-dir", str(self.task), "--repo", str(self.repo),
            "--prompt-file", str(self.prompt), "--codex-home", str(self.repo / "home"),
            "--timeout", "120", *flags])
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = dsp.run(args)
        finally:
            # run() records a dispatch marker under the real home; it belongs
            # to this throwaway lane, so it goes with it.
            dsp.dispatch_marker(self.repo).unlink(missing_ok=True)
        return rc, out.getvalue()

    def replies(self) -> list[str]:
        """Decisions the dispatcher sent back for the approval requests."""
        got = []
        for line in self.record.read_text(encoding="utf-8").splitlines():
            msg = json.loads(line)["in"]
            if msg.get("id", 0) > 1000 and "method" not in msg:
                got.append(msg["result"]["decision"])
        return got

    def steers(self) -> list[str]:
        return [json.loads(line)["in"]["params"]["input"][0]["text"]
                for line in self.record.read_text(encoding="utf-8").splitlines()
                if json.loads(line)["in"].get("method") == "turn/steer"]


def field_requests(lane_dir: str) -> list[dict]:
    return [{"method": CMD, "params": {"command": c, "cwd": lane_dir}} for c in FIELD]


def decide(task: Path, n: int, verdict: str, note: str = "") -> subprocess.CompletedProcess:
    argv = [sys.executable, str(DISPATCH), "--decide", str(task), str(n), verdict]
    return subprocess.run(argv + ([note] if note else []), capture_output=True, text=True)


def answer_when_asked(task: Path, answers: dict[int, tuple[str, str]]) -> threading.Thread:
    """Play the architect: answer each request once its .json appears."""
    def work():
        deadline = time.monotonic() + 60
        todo = dict(answers)
        while todo and time.monotonic() < deadline:
            for n, (verdict, note) in list(todo.items()):
                if (task / "approvals" / f"{n}.json").is_file():
                    decide(task, n, verdict, note)
                    del todo[n]
            time.sleep(0.1)
    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t


def main() -> int:
    checks: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane = Lane(root, field_requests(str(root / "lane")))
        rc, out = lane.run("--ask-timeout", "0")
        checks += [
            ("ask-timeout 0: the three field commands decline at once, rc=5",
             rc == 5 and lane.replies() == ["decline"] * 3),
            ("ask-timeout 0: no approvals/ directory, no [ask] line",
             not (lane.task / "approvals").exists() and "[ask]" not in out),
        ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane = Lane(root, field_requests(str(root / "lane")))
        started = time.monotonic()
        rc, out = lane.run("--ask-timeout", "1")
        took = time.monotonic() - started
        reqs = [json.loads((lane.task / "approvals" / f"{n}.json").read_text(encoding="utf-8"))
                for n in (1, 2, 3)]
        log = (lane.task / "RAW_OUTPUT.log").read_text(encoding="utf-8")
        final = (lane.task / "FINAL.txt").read_text(encoding="utf-8")
        checks += [
            ("no decision: each field command becomes [ask] 1..3 on stdout",
             all(f"[ask] {n} " in out for n in (1, 2, 3))),
            ("no decision: request files carry n, method, reason, command, cwd, at",
             all(set(r) >= {"n", "method", "reason", "command", "cwd", "at"} for r in reqs)
             and [r["n"] for r in reqs] == [1, 2, 3] and reqs[0]["command"] == FIELD[0]),
            ("no decision: timeout declines all three on the wire, rc=5",
             rc == 5 and lane.replies() == ["decline"] * 3),
            ("no decision: logged as timeouts, [decline] prefix kept",
             log.count("[ask-timeout]") == 3 and log.count("[decline] " + CMD) == 3
             and "ask timeout after 1s" in log),
            ("no decision: verdict names the timeouts", "3 by ask timeout" in final),
            ("no decision: waited about the timeout per request, not forever", took < 30),
        ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane = Lane(root, field_requests(str(root / "lane")))
        t = answer_when_asked(lane.task, {1: ("approve", ""), 2: ("decline", "use Select-String"),
                                          3: ("approve", "")})
        rc, out = lane.run("--ask-timeout", "30")
        t.join(5)
        final = (lane.task / "FINAL.txt").read_text(encoding="utf-8")
        checks += [
            ("mixed: architect answers reach the wire in order",
             lane.replies() == ["accept", "decline", "accept"]),
            ("mixed: one real decline keeps rc=5", rc == 5),
            ("mixed: verdict counts the decline and the architect approvals",
             "1 approvals declined" in final and "2 approved by architect" in final),
            ("mixed: the decline note reaches Codex as turn/steer",
             lane.steers() == ["The architect declined that request: use Select-String"]),
        ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane = Lane(root, field_requests(str(root / "lane")))
        t = answer_when_asked(lane.task, {n: ("approve", "") for n in (1, 2, 3)})
        rc, out = lane.run("--ask-timeout", "30")
        t.join(5)
        checks += [
            ("all approved: approved-by-architect is not a decline, rc=0",
             rc == 0 and lane.replies() == ["accept"] * 3 and "--- dispatch: OK (3 approved by architect) ---" in out),
            ("all approved: a second answer to the same request is refused",
             decide(lane.task, 1, "decline").returncode == 2),
            ("--decide refuses an unknown request number", decide(lane.task, 9, "approve").returncode == 2),
            ("--decide refuses a verdict other than approve|decline",
             decide(lane.task, 1, "maybe").returncode == 2),
        ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        outside = "C:/Users/x/.codex/auth.json" if os.name == "nt" else "/etc/passwd"
        lane = Lane(root, [{"method": CMD, "params": {"command": ["cmd", "/c", "type", outside],
                                                      "cwd": str(root / "lane")}}])
        rc, out = lane.run("--ask-timeout", "1")
        checks += [
            ("a true outside read is asked too, and declines on timeout - never auto-approved",
             rc == 5 and lane.replies() == ["decline"] and "[ask] 1 " in out),
        ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane = Lane(root, [{"method": CMD, "params": {"command": ["git", "status"],
                                                      "cwd": str(root / "lane")}}])
        rc, out = lane.run("--ask-timeout", "30")
        checks += [
            ("a request the filter approves is never asked",
             (rc, lane.replies()) == ((0, ["accept"]) if os.name == "nt" else (5, ["decline"]))
             and ("[ask]" not in out) == (os.name == "nt")),
        ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane_dir = str(root / "lane")
        lane = Lane(root, [])
        rc_run, _ = lane.run("--ask-timeout", "0")
        meta = json.loads((lane.task / "THREAD.json").read_text(encoding="utf-8"))
        checks.append(("run records the thread id, repo and home in THREAD.json",
                       rc_run == 0 and meta["thread_id"] == "thread-fake-1"
                       and Path(meta["repo"]) == lane.repo))
        before = lane.snapshot()
        lane.scenario([
            {"method": CMD, "params": {"command": ["git", "status"], "cwd": lane_dir}},
            {"method": "item/fileChange/requestApproval",
             "params": {"path": str(lane.repo / "x.txt")}},
            {"method": dsp.PERMISSION_APPROVAL, "params": {"permissions": {"network": {"enabled": True}}}},
        ], echo=True, answer_prefix="ANSWER")
        rc, out = lane.ask("why did you skip the probe?")
        wire = [json.loads(l)["in"] for l in lane.record.read_text(encoding="utf-8").splitlines()]
        resume = next((m for m in wire if m.get("method") == "thread/resume"), {})
        asklog = (lane.task / "ASK.log").read_text(encoding="utf-8")
        checks += [
            ("--ask resumes the recorded thread read-only",
             resume.get("params", {}).get("threadId") == "thread-fake-1"
             and resume["params"].get("sandbox") == "read-only"),
            ("--ask declines every request, even one the filter would approve",
             lane.results() == [{"decision": "decline"}, {"decision": "decline"},
                                {"permissions": {}, "scope": "turn"}]),
            ("--ask prints the answer and appends Q and A to ASK.log",
             rc == 0 and "ANSWER" in out and "why did you skip the probe?" in out
             and "Q: why did you skip the probe?" in asklog and "A: ANSWER" in asklog),
            ("--ask changes no lane file besides ASK.log", lane.snapshot() == before),
            ("--ask leaves no dispatch state behind (no FINAL.txt rewrite, no approvals/)",
             not (lane.task / "approvals").exists()),
        ]
        rc2, _ = lane.ask("again?")
        checks.append(("a second --ask appends", rc2 == 0 and (lane.task / "ASK.log").read_text(
            encoding="utf-8").count("===== ") == 2))
        (lane.task / "THREAD.json").unlink()
        (lane.task / "RAW_OUTPUT.log").write_text(
            '[item/started] {"threadId": "thread-old-0"}\n[x] {"threadId": "thread-fake-1"}\n',
            encoding="utf-8")
        lane.scenario([], echo=True, answer_prefix="ANSWER")
        rc3, out3 = lane.ask("old run?")
        checks.append(("without THREAD.json the last thread id in RAW_OUTPUT.log is used",
                       rc3 == 0 and "ANSWER" in out3))
        (lane.task / "RAW_OUTPUT.log").write_text('{"threadId": "thread-gone"}\n', encoding="utf-8")
        rc4, _ = lane.ask("gone?")
        checks.append(("a thread the server cannot resume fails with rc=1", rc4 == 1))
        (lane.task / "RAW_OUTPUT.log").write_text("nothing here\n", encoding="utf-8")
        rc5, _ = lane.ask("none?")
        checks.append(("no thread id at all is rc=2", rc5 == 2))
    failures = 0
    for name, ok in checks:
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  {name}")
    print(f"\n{len(checks) - failures}/{len(checks)} cases hold")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
