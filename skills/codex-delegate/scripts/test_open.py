#!/usr/bin/env python3
"""`--approvals open` (the default): everything approved except the
hard-to-undo list, which goes to [ask].

The list is an accident guard, not a containment boundary (owner decision,
25 Nov 2026: a Codex worker gets the same rights as Claude's sub-agents). So
these cases check two things only: what is on the list is held back, and what
is not on it - including writes outside the lane - goes through.

Run: python test_open.py    (exit 0 = every case holds)
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


with contextlib.redirect_stdout(io.StringIO()):
    ta = _load("ask_for_open", HERE / "test_ask.py")
dsp, FIELD = ta.dsp, ta.FIELD
LANE = Path("C:/lanes/demo") if sys.platform == "win32" else Path("/lanes/demo")
CMD = "item/commandExecution/requestApproval"
FILE = "item/fileChange/requestApproval"
PERM = dsp.PERMISSION_APPROVAL
PS = '"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command '

APPROVE = [
    *[(CMD, {"command": c, "cwd": "."}, "a 25 Nov field command") for c in FIELD],
    (CMD, {"command": ["git", "status"], "cwd": "."}, "plain git"),
    (CMD, {"command": ["git", "commit", "-m", "push the fix"], "cwd": "."}, "'push' in a message is not git push"),
    (CMD, {"command": ["git", "reset", "--hard", "HEAD~1"], "cwd": "."}, "reset --hard in the lane itself"),
    (CMD, {"command": ["git", "-C", ".delegate-runs/t1/tmp/repo", "clean", "-fdx"], "cwd": "."},
     "clean -f in a repo inside the lane"),
    (CMD, {"command": PS + "'Set-Content C:\\Users\\x\\proj\\out.txt hi'", "cwd": "."},
     "a write outside the lane is approved in open mode"),
    (CMD, {"command": PS + "'$d = [Environment]::GetFolderPath(\"Desktop\"); Set-Content \"$d\\x.txt\" hi'",
           "cwd": "."}, "a computed write target is approved in open mode"),
    (CMD, {"command": ["bash", "-lc", "rm -rf build"], "cwd": "."}, "recursive delete of a literal lane path"),
    (CMD, {"command": PS + "'Remove-Item -Recurse -Force .delegate-runs\\t1\\tmp'", "cwd": "."},
     "Remove-Item -Recurse of a lane path"),
    (CMD, {"command": ["cmd", "/c", "rd", "/s", "/q", "build"], "cwd": "."}, "rd /s of a lane path"),
    (CMD, {"command": PS + "'rm -Force old.txt'", "cwd": "."}, "-Force is not -Recurse"),
    (CMD, {"command": ["find", "build", "-name", "*.o", "-delete"], "cwd": "."}, "find -delete of a lane path"),
    (CMD, {"command": ["sh", "-c", "find -name '*.pyc' -delete"], "cwd": "."}, "find -delete from the lane cwd"),
    (CMD, {"command": PS + "'Get-Content ~/.claude/settings.json'", "cwd": "."}, "reading a live data folder"),
    (CMD, {"command": ["bash", "-lc", "echo x > ~/.codex-worker/note.txt"], "cwd": "."},
     ".codex-worker is not .codex"),
    (FILE, {"path": "C:/Users/x/proj/a.py" if sys.platform == "win32" else "/Users/x/proj/a.py"},
     "a file change outside the lane is approved"),
    (PERM, {"permissions": {"network": {"enabled": True}}}, "permissions are approved"),
]
ASK = [
    (CMD, {"command": ["git", "push"], "cwd": "."}, "git push"),
    (CMD, {"command": ["git", "push", "--force", "origin", "main"], "cwd": "."}, "git push --force"),
    (CMD, {"command": ["git", "push", "--force-with-lease"], "cwd": "."}, "git push --force-with-lease"),
    (CMD, {"command": ["git", "-C", "../other", "push", "-f"], "cwd": "."}, "git -C x push -f"),
    (CMD, {"command": ["bash", "-lc", "git commit -m x && git push origin HEAD"], "cwd": "."},
     "a push behind a commit"),
    (CMD, {"command": ["git", "-C", "C:/other" if sys.platform == "win32" else "/other", "reset", "--hard"],
           "cwd": "."}, "reset --hard on another repo"),
    (CMD, {"command": PS + "'cd ..\\other; git reset --hard'", "cwd": "."}, "cd, then reset --hard"),
    (CMD, {"command": ["git", "-C", "../x", "clean", "-fd"], "cwd": "."}, "clean -fd on another repo"),
    (CMD, {"command": ["git", "-C", "../x", "checkout", "--", "."], "cwd": "."}, "checkout -- . on another repo"),
    (CMD, {"command": ["git", "-C", "../x", "restore", "."], "cwd": "."}, "restore on another repo"),
    (CMD, {"command": ["git", "reset", "--hard"], "cwd": "../other"}, "reset --hard with a cwd outside the lane"),
    (CMD, {"command": ["bash", "-lc", "rm -rf /tmp/x"], "cwd": "."}, "rm -rf of an outside path"),
    (CMD, {"command": ["bash", "-lc", "rm -rf $DIR"], "cwd": "."}, "rm -rf of a variable"),
    (CMD, {"command": ["bash", "-lc", "rm -r ../sibling"], "cwd": "."}, "rm -r escaping the lane"),
    (CMD, {"command": PS + "'ri -r $target'", "cwd": "."}, "ri -r of a variable"),
    (CMD, {"command": PS + "'Remove-Item -Recurse'", "cwd": "."}, "a recursive delete with no target"),
    (CMD, {"command": PS + "'Set-Content ~/.unity_architect_ai/x 1'", "cwd": "."}, "write to .unity_architect_ai"),
    (CMD, {"command": PS + "'Remove-Item $env:USERPROFILE\\.unity_architect_ai\\db.sqlite'", "cwd": "."},
     "delete in .unity_architect_ai"),
    (CMD, {"command": ["bash", "-lc", "rm ~/.kimi-code/x"], "cwd": "."}, "delete in .kimi-code"),
    (CMD, {"command": ["cmd", "/c", "copy", "a", "C:\\Users\\x\\.cursor\\mcp.json"], "cwd": "."}, "copy into .cursor"),
    (CMD, {"command": ["bash", "-lc", "echo x > ~/.codex/config.toml"], "cwd": "."}, "write into .codex"),
    (CMD, {"command": PS + "'Add-Content C:\\Users\\x\\.claude\\settings.json x'", "cwd": "."}, "write into .claude"),
    (FILE, {"path": "C:\\Users\\x\\.claude\\settings.json"}, "a file change in .claude"),
    (FILE, {"path": "/home/x/.unity_architect_ai/token"}, "a file change in .unity_architect_ai"),
    (CMD, {"command": ["sh", "-c", "rm -rf /outside"], "cwd": "."}, "rm -rf of an absolute outside path"),
    (CMD, {"command": ["sh", "-c", "rm -r ../.."], "cwd": "."}, "rm -r ../.. escaping the lane"),
    (CMD, {"command": ["find", "/", "-delete"], "cwd": "."}, "find / -delete"),
    (CMD, {"command": ["sh", "-c", "find .. -name '*.tmp' -delete"], "cwd": "."}, "find .. -delete"),
    (CMD, {"command": ["find", "/tmp", "-exec", "rm", "{}", ";"], "cwd": "."}, "find /tmp -exec rm"),
    (CMD, {"command": ["sh", "-c", "find $D -delete"], "cwd": "."}, "find of a variable with -delete"),
]
# Windows-shaped targets (drive, backslash-relative): only paths on Windows,
# so run there and counted as skipped elsewhere.
WINDOWS_ASK = [
    (CMD, {"command": PS + "'Remove-Item -Recurse C:\\Users\\x\\proj'", "cwd": "."}, "Remove-Item -Recurse outside"),
    (CMD, {"command": ["cmd", "/c", "rd", "/s", "/q", "C:\\proj"], "cwd": "."}, "rd /s outside"),
    (CMD, {"command": ["cmd", "/c", "rmdir", "/s", "..\\x"], "cwd": "."}, "rmdir /s escaping the lane"),
    (CMD, {"command": ["cmd", "/c", "del", "/s", "C:\\x\\*.log"], "cwd": "."}, "del /s outside"),
]
if sys.platform == "win32":
    ASK += WINDOWS_ASK
SKIPPED_ON_POSIX = 0 if sys.platform == "win32" else len(WINDOWS_ASK)


def main() -> int:
    checks: list[tuple[str, bool]] = []
    for want, cases in (("approve", APPROVE), ("decline", ASK)):
        for method, params, why in cases:
            got, reason = dsp.open_decision(method, params, LANE)
            checks.append((f"{'approve' if want == 'approve' else 'ask    '}  {why}  ({reason})", got == want))

    # End to end: open is the default, and exit 5 only when something declined.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lane = ta.Lane(root, ta.field_requests(str(root / "lane"))
                       + [{"method": FILE, "params": {"path": str(root / "elsewhere.txt")}}])
        args = dsp.build_parser().parse_args(["--task-dir", "t", "--repo", "r", "--prompt-file", "p"])
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = dsp.run(dsp.build_parser().parse_args([
                    "--task-dir", str(lane.task), "--repo", str(lane.repo), "--prompt-file", str(lane.prompt),
                    "--codex-home", str(lane.repo / "home"), "--timeout", "120"]))
        finally:
            dsp.dispatch_marker(lane.repo).unlink(missing_ok=True)
        checks += [
            ("--approvals defaults to open", dsp.approvals_label(args) == ("open", "open (default)")),
            ("the default is announced as `[approvals] open (default)` on stdout and in the log",
             "[approvals] open (default)" in out.getvalue() and "[approvals] open (default)" in lane.log()),
            ("the verdict line names the mode", "--- dispatch: OK [approvals=open] ---" in out.getvalue()),
            ("open by default: the 3 field commands and an outside file change are approved, rc=0",
             rc == 0 and lane.replies() == ["accept"] * 4 and "[ask]" not in out.getvalue()),
        ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        push = {"method": CMD, "params": {"command": ["git", "push"], "cwd": str(root / "lane")}}
        lane = ta.Lane(root, [push])
        rc, out = lane.run("--approvals", "open", "--ask-timeout", "1")
        checks.append(("open: git push goes to [ask] and declines on timeout, rc=5",
                       rc == 5 and lane.replies() == ["decline"] and "[ask] 1 git push" in out))
        checks.append(("an explicit --approvals open is announced without (default), verdict names it",
                       "[approvals] open\n" in out and "(default)" not in out
                       and "[approvals=open] ---" in out))
        lane2 = ta.Lane(root / "b", [dict(push, params={**push["params"], "cwd": str(root / "b" / "lane")})])
        t = ta.when_asked(lane2, {1: ta.answer("approve")})
        rc2, out2 = lane2.run("--approvals", "open", "--ask-timeout", "30")
        t.join(5)
        checks.append(("open: a git push the architect approves goes through, rc=0",
                       rc2 == 0 and lane2.replies() == ["accept"]))
        lane3 = ta.Lane(root / "c", [dict(push, params={**push["params"], "cwd": str(root / "c" / "lane")})])
        rc3, _ = lane3.run("--approvals", "open", "--ask-timeout", "0")
        checks.append(("open with --ask-timeout 0: git push declines at once, rc=5",
                       rc3 == 5 and lane3.replies() == ["decline"]))

    failures = 0
    for name, ok in checks:
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  {name}")
    if SKIPPED_ON_POSIX:
        print(f"skipped on POSIX: {SKIPPED_ON_POSIX} Windows-shaped cases")
    print(f"\n{len(checks) - failures}/{len(checks)} cases hold")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
