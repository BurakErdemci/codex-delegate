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


def main() -> int:
    failures = 0
    for expected, method, params, why in CASES:
        got, reason = dsp.approval_decision(method, params, LANE)
        ok = got == expected
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  expected {expected:<7} got {got:<7} "
              f"({reason})\n        {why}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} cases hold")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
