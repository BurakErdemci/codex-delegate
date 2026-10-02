#!/usr/bin/env python3
"""Containment tests for dispatch.py's approval filter. No processes, no I/O.

`approval_decision` is pure precisely so this file can exist, and it needs to:
every regression below was found in the field, each one costing a whole worker
turn, and each one looked like the worker misbehaving rather than the filter
misjudging. A decline reason that names a path nobody wrote is the signature.

Run: python test_approvals.py    (exit 0 = every case holds)

Two halves that must both stay green. The DECLINE half is the containment
boundary - loosening the filter must not open it. The APPROVE half is the
false-positive boundary, which is the half that actually broke: a filter that
declines everything contains nothing, it just stops the work.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).with_name("dispatch.py")
_spec = importlib.util.spec_from_file_location("dispatch_under_test", _SRC)
dsp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dsp)

LANE = Path("C:/lanes/demo") if sys.platform == "win32" else Path("/lanes/demo")
OUTSIDE = "C:/project/Backend/venv/Scripts/python.exe"
CMD = "item/commandExecution/requestApproval"
FILE = "item/fileChange/requestApproval"

# (expected, method, params, why this case exists)
CASES = [
    # --- must APPROVE: false positives that cost real turns -----------------
    ("approve", CMD, {"command": ["cmd", "/c", "echo", "hi"], "cwd": "."},
     "cmd's own switch /c read as a POSIX-rooted path - blocked every "
     "cmd-wrapped command on Windows (5 Sep 2026)"),
    ("approve", CMD, {"command": ["cmd", "/v:on", "/c", "type", "README.md"], "cwd": "."},
     "switch with a :value is still a switch, not a path"),
    ("approve", CMD, {"command": ["sh", "-c", "./.delegate-runs/t1/py.sh -m pytest"], "cwd": "."},
     "dash switches were never the problem; guard against overcorrecting"),
    ("approve", CMD, {"command": r"""powershell -Command "Set-Content a.md 'x\n')'" """, "cwd": "."},
     r"here-string escape fragment \n') read as a UNC path (5 Sep 2026)"),
    ("approve", CMD, {"command": [str(LANE / "src/tool.exe"), "--out", "build/x"], "cwd": "."},
     "in-lane absolute paths are the normal case"),
    ("approve", FILE, {"path": str(LANE / ".delegate-runs/t1/turn-1.md"),
                       "content": f"acceptance: {OUTSIDE} -m pytest"},
     "file approvals judge the PATH only - content that quotes an outside "
     "path is text, not a write target. The field note claimed otherwise"),

    # --- must DECLINE: the containment boundary itself ----------------------
    ("decline", CMD, {"command": ["cmd", "/c", "type", OUTSIDE], "cwd": "."},
     "exempting switches must not exempt operands after them"),
    ("decline", CMD, {"command": ["cmd", "/c", "type", "/etc/passwd"], "cwd": "."},
     "/etc is a path, not a switch - the shape test has to tell them apart"),
    ("decline", CMD, {"command": ["bash", "-lc", f"{OUTSIDE} -m pytest"], "cwd": "."},
     "an out-of-lane interpreter reached through a shell is an operand"),
    ("decline", CMD, {"command": ["cmd", "/c", "type", "..\\..\\secrets.txt"], "cwd": "."},
     "relative escape out of the lane"),
    ("decline", CMD, {"command": ["cmd", "/c", "set", ">", "%TEMP%\\out.txt"], "cwd": "."},
     "location env vars are absolute paths in disguise"),
    ("decline", FILE, {"path": OUTSIDE},
     "a write outside the lane is the thing this filter is for"),
    ("decline", FILE, {"itemId": "x", "reason": None},
     "a payload we cannot scope is a payload we decline"),
    ("decline", "item/somethingNew/requestApproval", {"command": ["echo", "hi"]},
     "an unknown approval method must never be approved by accident"),
]


# The pre-dispatch spec gate, which must agree with the filter above. It once
# did not: the switch exemption lived in approval_decision, so the gate blocked
# `cmd /c ...` that the live filter approves. Both now share scan_tokens.
# (expected, ACCEPTANCE body, why)
ACCEPTANCE_CASES = [
    ("pass", "cmd /c .delegate-runs\\t1\\py.cmd -m pytest -q",
     "the bridged form the error message tells the architect to write"),
    ("pass", "sh .delegate-runs/t1/py.sh -m pytest -q", "same, POSIX side"),
    ("pass", "./scripts/gate.sh && npm test",
     "a blanket bullet-strip ate the leading ./ and read it as /scripts"),
    ("pass", "- ./scripts/gate.sh", "a real markdown bullet still comes off"),
    ("pass", "<ONE runnable, self-contained command.>",
     "an unfilled template must not block a dispatch"),
    ("block", "C:/proj/venv/Scripts/python.exe -m pytest",
     "the field failure: the main tree's venv, 2 turns, 0 files written"),
    ("block", "npm test > %TEMP%\\out.txt", "location env vars"),
    ("block", "cmd /c type /etc/passwd",
     "the switch exemption must not smuggle a real POSIX path past the gate"),
]


def check_acceptance() -> int:
    failures = 0
    for expected, body, why in ACCEPTANCE_CASES:
        verdict = dsp.acceptance_verdict(f"## ACCEPTANCE\n{body}\n", LANE)
        got = "pass" if verdict is None else "block"
        ok = got == expected
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  gate expected {expected:<5} got {got:<5} "
              f"({verdict[1] if verdict else 'clean'})\n        {why}")
    return failures


# The other pre-dispatch gate: a whitelisted EXISTING test file the spec does
# not authorize. Needs real files on disk, so it builds a throwaway tree.
# (expected, whitelist body, tests body, files to create, why)
TEST_EDIT_CASES = [
    ("block", "- src/a.py\n- tests/test_old.py", "Cover the new path.",
     ["tests/test_old.py"],
     "the measured failure: whitelisted, exists, unauthorized -> worker stops"),
    ("pass", "- src/a.py\n- tests/test_old.py",
     "Cover the new path.\nEXISTING TESTS I MAY MODIFY: tests/test_old.py",
     ["tests/test_old.py"], "authorized by the slot the template now ships"),
    ("pass", "- src/a.py\n- (new) tests/test_new.py", "Cover the new path.",
     [], "a test the worker creates was never under prohibition 4"),
    ("pass", "- src/a.py\n- tests/test_old.py", "EXISTING TESTS I MAY MODIFY: none",
     [], "whitelisted but not in the tree yet - the worker creates it"),
    ("pass", "- src/main.py\n- README.md", "EXISTING TESTS I MAY MODIFY: none",
     ["src/main.py", "README.md"], "no test files at all; the gate must stay quiet"),
    ("pass", "- .delegate-runs/t1/turn-*.md\n- src/a.py", "Cover it.",
     [], "the changelog glob every spec carries must never trip this"),
    ("block", "- app/__tests__/legacy.js", "Cover it.", ["app/__tests__/legacy.js"],
     "js-style test directory"),
    ("block", "- src/thing_test.go", "Cover it.", ["src/thing_test.go"],
     "go-style suffix"),
    ("pass", "- src/latest.py\n- src/contest.py", "Cover it.",
     ["src/latest.py", "src/contest.py"],
     "'test' inside a word is not a test file - the shape must not overreach"),
]


def check_test_edits(tmp: Path) -> int:
    failures = 0
    for i, (expected, wl, tests, files, why) in enumerate(TEST_EDIT_CASES):
        lane = tmp / f"case{i}"
        for rel in files:
            target = lane / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x", encoding="utf-8")
        lane.mkdir(parents=True, exist_ok=True)
        spec = f"# TASK t\n\n## FILE WHITELIST\n{wl}\n\n## TESTS\n{tests}\n"
        offenders = dsp.unauthorized_test_edits(spec, lane)
        got = "block" if offenders else "pass"
        ok = got == expected
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  tests-gate expected {expected:<5} "
              f"got {got:<5} ({offenders or 'clean'})\n        {why}")
    return failures


def main() -> int:
    failures = 0
    for expected, method, params, why in CASES:
        got, reason = dsp.approval_decision(method, params, LANE)
        ok = got == expected
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  expected {expected:<7} got {got:<7} "
              f"({reason})\n        {why}")
    failures += check_acceptance()
    with tempfile.TemporaryDirectory() as tmp:
        failures += check_test_edits(Path(tmp))
    total = len(CASES) + len(ACCEPTANCE_CASES) + len(TEST_EDIT_CASES)
    print(f"\n{total - failures}/{total} cases hold")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
