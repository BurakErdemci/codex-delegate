#!/usr/bin/env python3
"""Containment tests for dispatch.py's approval filter. No processes, no I/O,
except check_accept_env, which runs one generated ACCEPT.py in a temp lane.

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
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).with_name("dispatch.py")
_spec = importlib.util.spec_from_file_location("dispatch_under_test", _SRC)
dsp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dsp)

LANE = Path("C:/lanes/demo") if sys.platform == "win32" else Path("/lanes/demo")
WIN = sys.platform == "win32"
OUTSIDE = "C:/project/Backend/venv/Scripts/python.exe" if WIN else "/Users/x/project/venv/bin/python"
ESCAPE = "..\\..\\secrets.txt" if WIN else "../../secrets.txt"
GATE_VENV = "C:/proj/venv/Scripts/python.exe" if WIN else "/Users/x/proj/venv/bin/python"
TASK_TMP = ".delegate-runs/t1/tmp/repo"
CMD = "item/commandExecution/requestApproval"
FILE = "item/fileChange/requestApproval"

def _stub_is_repo(path: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(path))) == os.path.normcase(
        os.path.normpath(str(LANE / TASK_TMP)))


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
    ("decline", CMD, {"command": ["cmd", "/c", "type", ESCAPE], "cwd": "."},
     "relative escape out of the lane"),
    ("decline", CMD, {"command": ["cmd", "/c", "set", ">", "%TEMP%\\out.txt"], "cwd": "."},
     "location env vars are absolute paths in disguise"),
    ("decline", FILE, {"path": OUTSIDE},
     "a write outside the lane is the thing this filter is for"),
    ("decline", FILE, {"itemId": "x", "reason": None},
     "a payload we cannot scope is a payload we decline"),
    ("decline", "item/somethingNew/requestApproval", {"command": ["echo", "hi"]},
     "an unknown approval method must never be approved by accident"),

    # --- git policy: safe subset in the lane, anything but network in task tmp
    ("approve", CMD, {"command": ["git", "status"], "cwd": "."}, "reading is always fine"),
    ("approve", CMD, {"command": ["git", "diff", "--stat"], "cwd": "."}, "reading is always fine"),
    ("approve", CMD, {"command": ["git", "log", "--oneline", "-5"], "cwd": "."}, "reading is always fine"),
    ("approve", CMD, {"command": ["git", "add", "-A"], "cwd": "."}, "add on the lane's detached HEAD"),
    ("approve", CMD, {"command": ["git", "commit", "-m", "Add a"], "cwd": "."},
     "commit moves only the lane's own HEAD"),
    ("approve", CMD, {"command": ["git", "--no-pager", "-C", TASK_TMP, "init", "-q"], "cwd": "."},
     "temp test repos under .delegate-runs are the worker's own"),
    ("approve", CMD, {"command": ["git", "-C", TASK_TMP, "branch", "feature"], "cwd": "."},
     "branch is fine inside a temp repo"),
    ("approve", CMD, {"command": ["git", "stash"], "cwd": TASK_TMP}, "cwd inside task tmp counts too"),
    ("approve", CMD, {"command": ["bash", "-lc", "git status && git diff"], "cwd": "."},
     "every segment is safe"),
    ("decline", CMD, {"command": ["git", "push", "origin", "main"], "cwd": "."}, "network"),
    ("decline", CMD, {"command": ["git", "fetch"], "cwd": "."}, "network"),
    ("decline", CMD, {"command": ["git", "pull"], "cwd": "."}, "network"),
    ("decline", CMD, {"command": ["git", "remote", "add", "o", "x"], "cwd": "."}, "network config"),
    ("decline", CMD, {"command": ["git", "-C", ".delegate-runs/t1/tmp/notrepo", "branch", "-f", "main", "HEAD"],
                      "cwd": "."}, "a directory under .delegate-runs without its own .git resolves to the lane repo"),
    ("approve", CMD, {"command": ["git", "-C", ".delegate-runs/t1/tmp/notrepo", "init", "-q"], "cwd": "."},
     "init creates the temp repo"),
    ("approve", CMD, {"command": ["git", "-C", TASK_TMP + "/sub", "stash"], "cwd": "."},
     "a subdirectory of a temp repo belongs to it"),
    ("decline", CMD, {"command": ["git", "push"], "cwd": TASK_TMP}, "network is refused even in task tmp"),
    ("decline", CMD, {"command": ["git", "-C", TASK_TMP, "fetch"], "cwd": "."}, "same through -C"),
    ("decline", CMD, {"command": ["git", "branch", "-f", "main", "HEAD"], "cwd": "."}, "shared refs"),
    ("decline", CMD, {"command": ["git", "tag", "v1"], "cwd": "."}, "shared refs"),
    ("decline", CMD, {"command": ["git", "stash"], "cwd": "."}, "shared stash"),
    ("decline", CMD, {"command": ["git", "config", "user.name", "x"], "cwd": "."}, "shared config"),
    ("decline", CMD, {"command": ["git", "update-ref", "refs/heads/main", "HEAD"], "cwd": "."}, "shared refs"),
    ("decline", CMD, {"command": ["git", "gc"], "cwd": "."}, "shared object store"),
    ("decline", CMD, {"command": ["git", "worktree", "list"], "cwd": "."}, "shared worktree list"),
    ("decline", CMD, {"command": ["git", "checkout", "main"], "cwd": "."}, "moves HEAD off the base"),
    ("decline", CMD, {"command": ["git", "reset", "--hard", "HEAD~1"], "cwd": "."}, "history rewrite"),
    ("decline", CMD, {"command": ["git", "-c", "core.hooksPath=x", "commit", "-m", "a"], "cwd": "."},
     "global -c is declined"),
    ("decline", CMD, {"command": ["git", "-C", "/elsewhere", "status"], "cwd": "."}, "effective dir outside the lane"),
    ("decline", CMD, {"command": ["bash", "-lc", "cd src && git push"], "cwd": "."}, "segment after &&"),
    ("decline", CMD, {"command": ["bash", "-lc", "git status; git stash"], "cwd": "."}, "segment after ;"),
    ("decline", CMD, {"command": ["sh", "-c", "git commit -m x && git push origin HEAD"], "cwd": "."},
     "push hidden behind a safe commit"),

    # Probe regressions: indirect git must never inherit a wrapper's approval.
    ("decline", CMD, {"command": ["git", "push"], "cwd": "."}, "probe: network"),
    ("decline", CMD, {"command": ["git", "branch", "x"], "cwd": "."}, "probe: shared refs"),
    ("decline", CMD, {"command": ["git", "stash"], "cwd": "."}, "probe: shared stash"),
    ("decline", CMD, {"command": ["git", "config", "user.name", "x"], "cwd": "."},
     "probe: shared config"),
    ("decline", CMD, {"command": ["git", "-C", TASK_TMP, "push"], "cwd": "."},
     "probe: network even in a temp repo"),
    ("decline", CMD, {"command": ["env", "git", "branch", "x"], "cwd": "."},
     "probe: direct argv through env"),
    ("decline", CMD, {"command": ["/usr/bin/git", "branch", "x"], "cwd": "."},
     "probe: path-qualified git"),
    ("decline", CMD, {"command": ["./git", "branch", "x"], "cwd": "."},
     "probe: relative path-qualified git"),
    ("decline", CMD, {"command": ["C:/Program Files/Git/cmd/git.exe", "branch", "x"], "cwd": "."},
     "probe: Windows path-qualified git"),
    ("decline", CMD, {"command": ["git.exe", "branch", "x"], "cwd": "."},
     "probe: git.exe"),
    ("decline", CMD, {"command": ["bash", "-lc", "env git push"], "cwd": "."},
     "probe: shell through env"),
    ("decline", CMD, {"command": ["bash", "-lc", "command git branch x"], "cwd": "."},
     "probe: command builtin"),
    ("decline", CMD, {"command": ["bash", "-lc", "exec git tag v1"], "cwd": "."},
     "probe: exec builtin"),
    ("decline", CMD, {"command": ["bash", "-lc", "echo branch | xargs git"], "cwd": "."},
     "probe: pipeline into xargs"),
    ("decline", CMD, {"command": ["bash", "-lc", "$(echo git) branch x"], "cwd": "."},
     "probe: command substitution"),
    ("decline", CMD, {"command": ["bash", "-lc", "`echo git` branch x"], "cwd": "."},
     "probe: backtick substitution"),
    ("decline", CMD, {"command": ["bash", "-lc", "GIT_DIR=/elsewhere/.git git status"], "cwd": "."},
     "probe: GIT_DIR overrides the repository"),
    ("decline", CMD, {"command": ["bash", "-lc", "GIT_WORK_TREE=/x git add ."], "cwd": "."},
     "probe: GIT_WORK_TREE overrides the worktree"),
    ("decline", CMD, {"command": ["bash", "-lc", "/usr/bin/git -C . branch x"], "cwd": "."},
     "probe: path-qualified git in a shell"),
    ("decline", CMD, {"command": ["sh", "-c", "nice git branch x"], "cwd": "."},
     "probe: nice wrapper"),
    ("decline", CMD, {"command": ["bash", "-lc", "git status; /usr/local/bin/git push"], "cwd": "."},
     "probe: path-qualified git after a safe segment"),
    ("decline", CMD, {"command": ["sudo", "git", "status"], "cwd": "."},
     "sudo is indirect even for a safe subcommand"),
    ("decline", CMD, {"command": "time git status", "cwd": "."},
     "time is indirect in a command string"),
    ("decline", CMD, {"command": ["env", "'git.exe'", "status"], "cwd": "."},
     "quotes and an exe suffix do not hide a wrapped executable"),
    ("decline", CMD, {"command": ["env", "tools/git.exe", "status"], "cwd": "."},
     "the basename identifies an indirect executable"),
    ("decline", CMD, {"command": ["GIT_COMMON_DIR=elsewhere", "git", "status"], "cwd": "."},
     "direct argv assignments override shared repository state"),
    ("decline", CMD, {"command": "OK=1 GIT_INDEX_FILE=index git status", "cwd": "."},
     "all leading assignments are checked"),
    ("decline", CMD, {"command": "GIT_CONFIG=config git status", "cwd": "."},
     "GIT_CONFIG redirects configuration"),
    ("decline", CMD, {"command": "GIT_CONFIG_COUNT=1 echo hi", "cwd": "."},
     "GIT_CONFIG prefixes are declined regardless of subcommand"),
    ("decline", CMD, {"command": "GIT_OBJECT_DIRECTORY=objects git status", "cwd": "."},
     "object directory assignment changes the repository boundary"),
    ("decline", CMD, {"command": "echo $(echo digit)", "cwd": "."},
     "substitution plus the git substring cannot be judged lexically"),
    ("approve", CMD, {"command": "OK=1 git status", "cwd": "."},
     "ordinary leading assignments leave a direct git invocation"),
    ("approve", CMD, {"command": ["bash", "-lc", "echo 'git'"], "cwd": "."},
     "quoted git in an ordinary command is text"),
    ("approve", CMD, {"command": ["bash", "-lc", "echo 'git status; git branch'"], "cwd": "."},
     "quoted separators and mentions must remain inside the argument"),
    ("approve", CMD, {"command": ["echo", "git status"], "cwd": "."},
     "a direct argv argument mentioning git is not an executable token"),
    ("approve", CMD, {"command": ["bash", "-lc", "echo $(echo hi)"], "cwd": "."},
     "substitution without the git substring is outside the git rule"),
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
    ("block", f"{GATE_VENV} -m pytest",
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


# Runs inside the generated ACCEPT.py's children. Exits non-zero, naming the
# first variable that does not point into the lane's tmp/accept/.
_ENV_PROBE = r'''
import os, shlex, sys, tempfile
from pathlib import Path
want = Path(os.environ["EXPECT_SCRATCH"]).resolve()
bad = [k for k in ("TEMP", "TMP", "TMPDIR") if Path(os.environ.get(k, "")).resolve() != want]
if Path(tempfile.gettempdir()).resolve() != want:
    bad.append("tempfile.gettempdir=" + tempfile.gettempdir())
if os.environ.get("PYTHONDONTWRITEBYTECODE") != "1":
    bad.append("PYTHONDONTWRITEBYTECODE")
opts = shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))
if opts[:1] != ["--preexisting"] or opts[1:3] != ["-p", "no:cacheprovider"]:
    bad.append("PYTEST_ADDOPTS=" + os.environ.get("PYTEST_ADDOPTS", ""))
if opts[3:] != ["--basetemp=" + (want / "pytest").as_posix()]:
    bad.append("basetemp " + repr(opts[3:]))
with tempfile.NamedTemporaryFile(delete=False) as fh:
    if Path(fh.name).resolve().parent != want:
        bad.append("temp file landed at " + fh.name)
print("TEMP=" + os.environ.get("TEMP", ""))
sys.exit("bad: " + ", ".join(bad) if bad else 0)
'''


def check_accept_env(tmp: Path) -> int:
    """The generated ACCEPT.py gives every probe and command lane-local temp dirs.

    Workers used to set TEMP/TMP themselves, and the filter above declines
    that by design; this pins the replacement, so the worker never has to.
    """
    spec = importlib.util.spec_from_file_location("new_lane_under_test",
                                                  Path(__file__).with_name("new-lane.py"))
    nl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nl)
    td = tmp / "lane" / ".delegate-runs" / "t1"
    (td / "probes").mkdir(parents=True)
    (td / "turn-1.md").write_text("# RUN t1 / turn 1\nfilled in\n", encoding="utf-8")
    (td / "probes" / "env_probe.py").write_text(_ENV_PROBE, encoding="utf-8")
    probe_copy = tmp / "env_probe_cmd.py"
    probe_copy.write_text(_ENV_PROBE, encoding="utf-8")
    nl.write_accept(td, {"commands": [[sys.executable, str(probe_copy)]]}, [])
    scratch = td / "tmp" / "accept"
    env = dict(os.environ, PYTEST_ADDOPTS="--preexisting", EXPECT_SCRATCH=str(scratch))
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    r = subprocess.run([sys.executable, str(td / "ACCEPT.py")], env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300)
    out = r.stdout + r.stderr
    checks = [
        ("ACCEPT.py exits 0 with ACCEPT ALL OK", r.returncode == 0 and out.rstrip().endswith("ACCEPT ALL OK")),
        ("probe saw TEMP/TMP/TMPDIR/pytest opts in the lane", "ok   probe env_probe.py" in out),
        ("command saw the same", "ok   command " in out),
        ("tmp/accept removed after a pass", not scratch.exists() and (td / "tmp").is_dir()),
    ]
    failures = 0
    for name, ok in checks:
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  accept-env {name}")
    if failures:
        print(out[-3000:])
    return failures


def check_brief_line() -> int:
    """PROMPT.txt tells the worker not to set TEMP itself, and the plain command passes."""
    spec = importlib.util.spec_from_file_location("new_lane_brief",
                                                  Path(__file__).with_name("new-lane.py"))
    nl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nl)
    prompt = nl.build_prompt("worker", "t1", 1, False)
    accept = "## ACCEPTANCE\n- `python .delegate-runs/t1/ACCEPT.py`\n"
    checks = [
        ("contract forbids setting TEMP", "Never set TEMP, TMP or TMPDIR yourself" in prompt),
        ("contract gives the basetemp argument", "--basetemp=.delegate-runs/<task-id>/tmp/pytest" in prompt),
        ("plain acceptance command passes the spec gate", dsp.acceptance_verdict(accept, LANE) is None),
    ]
    failures = 0
    for name, ok in checks:
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  brief {name}")
    return failures


ACCEPT_ENV_CHECKS = 4
BRIEF_CHECKS = 3


def main() -> int:
    failures = 0
    for expected, method, params, why in CASES:
        got, reason = dsp.approval_decision(method, params, LANE, is_repo=_stub_is_repo)
        ok = got == expected
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  expected {expected:<7} got {got:<7} "
              f"({reason})\n        {why}")
    failures += check_acceptance()
    with tempfile.TemporaryDirectory() as tmp:
        failures += check_test_edits(Path(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        failures += check_accept_env(Path(tmp))
    failures += check_brief_line()
    total = (len(CASES) + len(ACCEPTANCE_CASES) + len(TEST_EDIT_CASES)
             + ACCEPT_ENV_CHECKS + BRIEF_CHECKS)
    print(f"\n{total - failures}/{total} cases hold")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
