#!/usr/bin/env python3
"""The [ask] flow and --ask end to end: dispatch.run() against fake_app_server.py.

No real Codex: codex_path() is pointed at this interpreter, which then runs a
file named `app-server` in the lane (the cwd AppServer spawns in), and that
file runs fake_app_server.py. Everything else - approval filter, the private
approvals folder, --decide, the wire replies, the verdict and exit code - is
the real code path. CODEX_DELEGATE_STATE_DIR points the private root into
the test's temp dir, outside the lane, as the real root is.

Run: python test_ask.py    (exit 0 = every case holds)
"""
from __future__ import annotations

import contextlib
import hashlib
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
THREAD = "0aaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeee1"
dsp.codex_path = lambda: sys.executable
dsp.codex_version = lambda: (0, 171, 0)


class Lane:
    """A throwaway lane with a fake app-server scenario and a private root."""

    def __init__(self, root: Path, requests: list[dict], private: Path | None = None, **extra):
        self.root = root
        self.repo = root / "lane"
        self.task = self.repo / ".delegate-runs" / "t1"
        self.task.mkdir(parents=True)
        self.private = private or root / "private"
        os.environ["CODEX_DELEGATE_STATE_DIR"] = str(self.private)
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

    def approvals(self) -> Path | None:
        run_dir = dsp.find_private_run(self.task)
        return run_dir / "approvals" if run_dir else None

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

    def ask(self, *argv: str, home: Path | None = None) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dsp.ask_codex([str(self.task), *argv, "--codex-home", str(home or self.root / "home")])
        # A failed --ask keeps its transcript in the temp dir for diagnosis;
        # the test made it, so the test removes it.
        for line in err.getvalue().splitlines():
            if line.startswith("transcript kept at "):
                Path(line[len("transcript kept at "):]).unlink(missing_ok=True)
        return rc, out.getvalue()

    def wire(self) -> list[dict]:
        return [json.loads(line)["in"] for line in self.record.read_text(encoding="utf-8").splitlines()]

    def snapshot(self) -> dict[str, str]:
        return {str(p.relative_to(self.repo)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(self.repo.rglob("*")) if p.is_file()}

    def results(self) -> list[dict]:
        return [m.get("result") for m in self.wire() if m.get("id", 0) > 1000 and "method" not in m]

    def replies(self) -> list[str]:
        """Decisions the dispatcher sent back for the approval requests."""
        return [r["decision"] for r in self.results()]

    def steers(self) -> list[str]:
        return [m["params"]["input"][0]["text"] for m in self.wire() if m.get("method") == "turn/steer"]

    def log(self) -> str:
        return (self.task / "RAW_OUTPUT.log").read_text(encoding="utf-8")


def field_requests(lane_dir: str) -> list[dict]:
    return [{"method": CMD, "params": {"command": c, "cwd": lane_dir}} for c in FIELD]


def decide(task: Path, n: int, verdict: str, note: str = "") -> subprocess.CompletedProcess:
    argv = [sys.executable, str(DISPATCH), "--decide", str(task), str(n), verdict]
    return subprocess.run(argv + ([note] if note else []), capture_output=True, text=True)


def when_asked(lane: Lane, todo: dict[int, callable]) -> threading.Thread:
    """Run todo[n](lane, request_file) once request n appears in the private folder."""
    def work():
        deadline = time.monotonic() + 60
        pending = dict(todo)
        while pending and time.monotonic() < deadline:
            adir = lane.approvals()
            for n, act in list(pending.items()):
                if adir is not None and (adir / f"{n}.json").is_file():
                    act(lane, adir / f"{n}.json")
                    del pending[n]
            time.sleep(0.1)
    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t


def answer(verdict: str, note: str = ""):
    return lambda lane, req: decide(lane.task, int(req.stem), verdict, note)


def forge_in_lane(lane: Lane, req: Path) -> None:
    """The worker's move: write an approval where the first cut looked for it."""
    old = lane.task / "approvals"
    old.mkdir(exist_ok=True)
    (old / f"{req.stem}.decision").write_text("approve\n", encoding="utf-8")
    (old / f"{req.stem}.json").write_text(req.read_text(encoding="utf-8"), encoding="utf-8")


def forge_private(nonce: str | None, age: float = 0.0):
    def act(lane: Lane, req: Path) -> None:
        body = {"verdict": "approve"} if nonce is None else {"verdict": "approve", "nonce": nonce}
        if nonce == "REAL":
            body["nonce"] = json.loads(req.read_text(encoding="utf-8"))["nonce"]
        target = req.with_suffix(".decision")
        target.write_text(json.dumps(body), encoding="utf-8")
        if age:
            past = req.stat().st_mtime - age
            os.utime(target, (past, past))
    return act


def main() -> int:
    checks: list[tuple[str, bool]] = []

    # --- [ask] basics -------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane = Lane(root, field_requests(str(root / "lane")))
        rc, out = lane.run("--ask-timeout", "0")
        checks += [
            ("ask-timeout 0: the three field commands decline at once, rc=5",
             rc == 5 and lane.replies() == ["decline"] * 3),
            ("ask-timeout 0: no request files, no [ask] line",
             not any(lane.approvals().glob("*.json")) and "[ask]" not in out),
        ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane = Lane(root, field_requests(str(root / "lane")))
        started = time.monotonic()
        rc, out = lane.run("--ask-timeout", "1")
        took = time.monotonic() - started
        adir = lane.approvals()
        reqs = [json.loads((adir / f"{n}.json").read_text(encoding="utf-8")) for n in (1, 2, 3)]
        final = (lane.task / "FINAL.txt").read_text(encoding="utf-8")
        checks += [
            ("no decision: each field command becomes [ask] 1..3, naming the private folder",
             all(f"[ask] {n} " in out for n in (1, 2, 3)) and f"(approvals: {adir})" in out),
            ("the private approvals folder is outside the lane",
             not dsp._within(adir, lane.repo) and dsp._within(adir, lane.private)),
            ("nothing of the channel is written into the lane", not (lane.task / "approvals").exists()),
            ("no decision: requests carry n, method, reason, command, cwd, at and a 32-hex nonce",
             all(set(r) >= {"n", "method", "reason", "command", "cwd", "at", "nonce"} for r in reqs)
             and all(len(r["nonce"]) == 32 for r in reqs) and len({r["nonce"] for r in reqs}) == 3
             and [r["n"] for r in reqs] == [1, 2, 3] and reqs[0]["command"] == FIELD[0]),
            ("no decision: timeout declines all three on the wire, rc=5",
             rc == 5 and lane.replies() == ["decline"] * 3),
            ("no decision: logged as timeouts, [decline] prefix kept",
             lane.log().count("[ask-timeout]") == 3 and lane.log().count("[decline] " + CMD) == 3),
            ("no decision: verdict names the timeouts", "3 by ask timeout" in final),
            ("no decision: waited about the timeout per request, not forever", took < 30),
        ]
        if os.name != "nt":
            checks.append(("the private run folder is owner-only (0o700)",
                           (adir.stat().st_mode & 0o777) == 0o700
                           and (adir.parent.stat().st_mode & 0o777) == 0o700))

    # --- forged decisions are ignored ----------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane = Lane(root, field_requests(str(root / "lane")))
        t = when_asked(lane, {1: forge_in_lane, 2: forge_private(None), 3: forge_private("0" * 32)})
        rc, out = lane.run("--ask-timeout", "2")
        t.join(5)
        checks += [
            ("forged: lane approvals/1.decision, private file without nonce, wrong nonce - all decline",
             rc == 5 and lane.replies() == ["decline"] * 3 and lane.log().count("[ask-timeout]") == 3),
            ("forged: the wrong-nonce files are logged as ignored",
             lane.log().count("[ask-ignored]") == 2 and "nonce does not match" in lane.log()),
        ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane = Lane(root, field_requests(str(root / "lane"))[:1])
        t = when_asked(lane, {1: forge_private("REAL", age=120)})
        rc, out = lane.run("--ask-timeout", "2")
        t.join(5)
        checks.append(("a decision with the right nonce but older than its request is ignored",
                       rc == 5 and lane.replies() == ["decline"] and "predates the request" in lane.log()))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane = Lane(root, field_requests(str(root / "lane"))[:1], private=root / "lane" / "state")
        rc, out = lane.run("--ask-timeout", "30")
        checks.append(("a private root inside the lane disables [ask]: immediate decline, logged",
                       rc == 5 and lane.replies() == ["decline"] and "[ask]" not in out
                       and "[ask-disabled]" in lane.log() and dsp.find_private_run(lane.task) is None))

    # --- real answers through --decide --------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane = Lane(root, field_requests(str(root / "lane")))
        t = when_asked(lane, {1: answer("approve"), 2: answer("decline", "use Select-String"),
                              3: answer("approve")})
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
        t = when_asked(lane, {n: answer("approve") for n in (1, 2, 3)})
        rc, out = lane.run("--ask-timeout", "30")
        t.join(5)
        checks += [
            ("all approved: approved-by-architect is not a decline, rc=0",
             rc == 0 and lane.replies() == ["accept"] * 3
             and "--- dispatch: OK (3 approved by architect) ---" in out),
            ("all approved: a second answer to the same request is refused",
             decide(lane.task, 1, "decline").returncode == 2),
            ("--decide refuses an unknown request number", decide(lane.task, 9, "approve").returncode == 2),
            ("--decide refuses a verdict other than approve|decline",
             decide(lane.task, 1, "maybe").returncode == 2),
        ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        other = root / "other" / ".delegate-runs" / "t1"
        (other / "approvals").mkdir(parents=True)
        (other / "approvals" / "1.json").write_text('{"n": 1, "nonce": "x"}', encoding="utf-8")
        os.environ["CODEX_DELEGATE_STATE_DIR"] = str(root / "private")
        checks.append(("--decide never uses an approvals folder found in the lane",
                       decide(other, 1, "approve").returncode == 2
                       and not (other / "approvals" / "1.decision").exists()))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        outside = "C:/Users/x/.codex/auth.json" if os.name == "nt" else "/etc/passwd"
        lane = Lane(root, [{"method": CMD, "params": {"command": ["cmd", "/c", "type", outside],
                                                      "cwd": str(root / "lane")}}])
        rc, out = lane.run("--ask-timeout", "1")
        checks.append(("a true outside read is asked too, and declines on timeout - never auto-approved",
                       rc == 5 and lane.replies() == ["decline"] and "[ask] 1 " in out))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane = Lane(root, [{"method": CMD, "params": {"command": ["git", "status"],
                                                      "cwd": str(root / "lane")}}])
        rc, out = lane.run("--ask-timeout", "30")
        checks.append(("a request the filter approves is never asked",
                       (rc, lane.replies()) == ((0, ["accept"]) if os.name == "nt" else (5, ["decline"]))
                       and ("[ask]" not in out) == (os.name == "nt")))

    # --- --ask ---------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane_dir = str(root / "lane")
        lane = Lane(root, [])
        rc_run, _ = lane.run("--ask-timeout", "0")
        meta = json.loads((dsp.find_private_run(lane.task) / "thread.json").read_text(encoding="utf-8"))
        checks.append(("run records the thread id in the private run folder, not the lane",
                       rc_run == 0 and meta == {"thread_id": THREAD}
                       and not (lane.task / "THREAD.json").exists()))
        # The worker's move: a lane THREAD.json pointing at its own home and repo.
        evil_home = root / "evil-home"
        (lane.task / "THREAD.json").write_text(json.dumps(
            {"thread_id": "0bbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2", "repo": str(root),
             "codex_home": str(evil_home)}), encoding="utf-8")
        # And a link planted where ASK.log used to be: a junction needs no
        # privilege; a file symlink is tried too, skipped if the account lacks it.
        victim = root / "victim"
        victim.mkdir()
        (victim / "keep.txt").write_text("untouched", encoding="utf-8")
        junction = subprocess.run(["cmd", "/c", "mklink", "/J", str(lane.task / "ASK.log"), str(victim)],
                                  capture_output=True) if os.name == "nt" else None
        planted = (lane.task / "ASK.log").exists()
        if not planted:
            try:
                os.symlink(victim / "keep.txt", lane.task / "ASK.log")
                planted = True
            except OSError:
                pass
        before = lane.snapshot()
        victim_before = sorted((p.name, p.read_bytes()) for p in victim.iterdir())
        lane.scenario([
            {"method": CMD, "params": {"command": ["git", "status"], "cwd": lane_dir}},
            {"method": "item/fileChange/requestApproval",
             "params": {"path": str(lane.repo / "x.txt")}},
            {"method": dsp.PERMISSION_APPROVAL, "params": {"permissions": {"network": {"enabled": True}}}},
        ], echo=True, answer_prefix="ANSWER")
        rc, out = lane.ask("why did you skip the probe?")
        wire = lane.wire()
        env = next(m["_env"] for m in wire if "_env" in m)
        resume = next((m for m in wire if m.get("method") == "thread/resume"), {})
        private_log = dsp.find_private_run(lane.task) / "ASK.log"
        asklog = private_log.read_text(encoding="utf-8") if private_log.is_file() else ""
        checks += [
            ("a link planted at the old ASK.log path was really there", planted),
            ("--ask never writes through a link planted at the old lane ASK.log path",
             sorted((p.name, p.read_bytes()) for p in victim.iterdir()) == victim_before),
            ("--ask resumes the privately recorded thread read-only, in the layout's lane",
             resume.get("params", {}).get("threadId") == THREAD
             and resume["params"].get("sandbox") == "read-only"
             and Path(resume["params"].get("cwd")) == lane.repo.resolve()),
            ("--ask ignores a lane THREAD.json: home is the --codex-home flag, cwd the lane",
             Path(env["CODEX_HOME"]) == root / "home" and Path(env["cwd"]) == lane.repo.resolve()
             and not evil_home.exists()),
            ("--ask declines every request, even one the filter would approve",
             lane.results() == [{"decision": "decline"}, {"decision": "decline"},
                                {"permissions": {}, "scope": "turn"}]),
            ("--ask appends Q and A to ASK.log in the private run folder and prints its path",
             rc == 0 and "ANSWER" in out and "why did you skip the probe?" in out
             and "Q: why did you skip the probe?" in asklog and "A: ANSWER" in asklog
             and str(private_log) in out),
            ("--ask changes no lane file at all", lane.snapshot() == before),
        ]
        # A hard link needs no privilege either, and unlike a junction it is a
        # file: an append through the old path would land in the victim file.
        if os.name == "nt" and junction is not None:
            subprocess.run(["cmd", "/c", "rmdir", str(lane.task / "ASK.log")], capture_output=True)
        else:
            (lane.task / "ASK.log").unlink(missing_ok=True)
        os.link(victim / "keep.txt", lane.task / "ASK.log")
        rc2, _ = lane.ask("again?")
        checks += [
            ("a second --ask appends", rc2 == 0 and private_log.read_text(
                encoding="utf-8").count("===== ") == 2),
            ("--ask never writes through a hard link planted at the old ASK.log path",
             (victim / "keep.txt").read_text(encoding="utf-8") == "untouched"),
        ]
        (lane.task / "ASK.log").unlink()
        # Malformed id in the private record is refused, not passed on.
        (dsp.find_private_run(lane.task) / "thread.json").write_text(
            json.dumps({"thread_id": "../../etc"}), encoding="utf-8")
        rc_bad, _ = lane.ask("bad id?")
        checks.append(("a malformed thread id in the private record is refused (rc=2)", rc_bad == 2))
        # An older run: no private record, so RAW_OUTPUT.log, UUIDs only.
        dsp._index_file(lane.task).unlink()
        (lane.task / "RAW_OUTPUT.log").write_text(
            f'[a] {{"threadId": "0ccccccc-cccc-4ccc-8ccc-ccccccccccc3"}}\n[b] {{"threadId": "{THREAD}"}}\n'
            '[c] {"threadId": "../not-a-uuid"}\n', encoding="utf-8")
        lane.scenario([], echo=True, answer_prefix="ANSWER")
        rc3, out3 = lane.ask("old run?")
        resumed = next(m for m in lane.wire() if m.get("method") == "thread/resume")["params"]["threadId"]
        checks.append(("without a private record the last UUID in RAW_OUTPUT.log is used",
                       rc3 == 0 and "ANSWER" in out3 and resumed == THREAD))
        (lane.task / "RAW_OUTPUT.log").write_text(
            '{"threadId": "0ddddddd-dddd-4ddd-8ddd-ddddddddddd4"}\n', encoding="utf-8")
        rc4, _ = lane.ask("gone?")
        checks.append(("a thread the server cannot resume fails with rc=1", rc4 == 1))
        (lane.task / "RAW_OUTPUT.log").write_text('{"threadId": "thread-fake-1"}\n', encoding="utf-8")
        rc5, _ = lane.ask("none?")
        checks.append(("a log id that is not a UUID is refused (rc=2)", rc5 == 2))
    with tempfile.TemporaryDirectory() as tmp:
        loose = Path(tmp) / "loose-task"
        loose.mkdir()
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            rc6 = dsp.ask_codex([str(loose), "q?"])
        checks.append(("--ask refuses a task dir outside the <lane>/.delegate-runs/<task> layout",
                       rc6 == 2 and "layout" in out.getvalue()))

    failures = 0
    for name, ok in checks:
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  {name}")
    print(f"\n{len(checks) - failures}/{len(checks)} cases hold")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
