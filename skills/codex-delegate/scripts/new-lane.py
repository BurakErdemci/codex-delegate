#!/usr/bin/env python3
"""Scaffold, re-seed and close a lane - the six steps that carry no judgment.

Opening a lane by hand is: worktree add, verify the base SHA, trust the path,
make the run dir, seed the changelog skeleton, assemble PROMPT.txt. Measured:
six lanes in one session, six passes through that list by hand, and every
step of it silently wrong-able. Forget the skeleton and the turn is rejected
mechanically at acceptance; forget the contract copy and the worker never
learns the format; branch from a stale base and nothing says so at all.

What stays with the architect is the part that needs judgment: SPEC.md. This
script refuses to print a dispatch line for a lane whose SPEC.md is missing or
still a skeleton, because a dispatched empty spec costs a whole turn.

  open   create the worktree, trust it, seed turn 1, build PROMPT.txt
  turn   seed the next turn's skeleton and re-point PROMPT.txt at it (retries,
         recovery) - the architect owns the turn counter, the worker cannot
         know it across a cold start
  close  archive the contract, remove the worktree, drop the trust entry
  blind-open / blind-close
         a detached blind-audit worktree with the config links and a verify/
         dir, and its removal - no task dir, no trust entry

`close` is not optional and not deferrable: 9 stale worktrees, 8 branches and a
growing trust list were the measured cost of leaving it to memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent      # skills/codex-delegate
AUDIT_ROOT = SKILL_ROOT.parent / "codex-audit"           # sibling skill
DOCTOR = Path(__file__).resolve().parent / "doctor.py"
DISPATCH = Path(__file__).resolve().parent / "dispatch.py"

MODES = ("worker", "audit", "research")

SKELETON_MARKER = "SKELETON"
# Kept byte-identical to codex-delegate SKILL.md §4's printf. The acceptance
# gate, the architect's own check and this seeder all read the same marker;
# three writers of one string is how it drifts, so change all three together.
SKELETON = ("# RUN {task_id} / turn {turn}\n"
            "status: SKELETON - worker has not filled this in\n")

INSTRUCTION = ("Read .delegate-runs/{task_id}/SPEC.md and execute it. Task dir: "
               ".delegate-runs/{task_id}/ - this is turn {turn}; your changelog skeleton "
               "is at .delegate-runs/{task_id}/turn-{turn}.md - fill it in.")

RECOVERY_INSTRUCTION = (
    "Read .delegate-runs/{task_id}/SPEC.md. Assess the current working tree against it "
    "and complete what is missing. This is turn {turn}; your changelog skeleton is at "
    ".delegate-runs/{task_id}/turn-{turn}.md - fill it in.")

RESEARCH_NOTE = """
NOTE FOR THIS TASK: this is a RESEARCH task, not an implementation task. It
produces no product code. Everything above still applies - the file whitelist,
the changelog, the acceptance command, the six-line final message - but "what
you changed" means "what you investigated and wrote down".
"""

AUDIT_NOTE = """
NOTE FOR THIS TASK: this is an AUDIT task. You produce no product code and you
fix nothing. Your output is findings files and runnable probes, on disk, under
the task dir. The contract below is binding and is not a summary of it.
"""


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(args: list[str], cwd: Path | None = None, check: bool = True) -> str:
    out = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    if check and out.returncode != 0:
        raise SystemExit(f"BLOCK: git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def repo_root(start: Path) -> Path:
    return Path(git(["rev-parse", "--show-toplevel"], cwd=start))


def main_repo_of(lane: Path) -> Path:
    """The MAIN worktree behind a lane - never `--show-toplevel` from inside it.

    Measured in review, and it destroyed data: `close` resolved the repo from
    the cwd, so running it from inside the lane made the archive a directory
    *inside the lane*, and the next line deleted the worktree with the archive
    in it. SPEC.md, both changelogs, findings/ and probes/ were gone, with no
    copy in the main repo. Standing in the lane you are closing is the normal
    way to close a lane, so the cwd is the one thing that must not decide this.

    --git-common-dir points at the main repo's .git from any linked worktree.
    """
    common = Path(git(["rev-parse", "--git-common-dir"], cwd=lane))
    if not common.is_absolute():                    # older git answers relatively
        common = (lane / common).resolve()
    return common.parent


def is_linked_worktree(path: Path) -> bool:
    return repo_root(path) != main_repo_of(path)


def registered_worktree(repo: Path, lane: Path) -> bool:
    """Does git still list this lane, even though the directory is gone?"""
    listing = git(["worktree", "list", "--porcelain"], cwd=repo, check=False)
    return any(line.startswith("worktree ")
               and Path(line.split(" ", 1)[1]).resolve() == lane
               for line in listing.splitlines())


def live_worktree(path: Path) -> bool:
    """Does git still serve this directory as its own worktree?

    False for the debris of a half-failed `git worktree remove`: measured,
    a "Permission denied" (a shell or hung process with its cwd inside the
    lane) unregistered the worktree and left part of the directory, and
    every later close stopped at "not a git worktree".
    """
    if not (path / ".git").exists():
        return False
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(path),
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    return out.returncode == 0 and Path(out.stdout.strip()).resolve() == path


def lanes_root(repo: Path) -> Path:
    return (repo.parent / f"{repo.name}-lanes").resolve()


def leftover_repo(path: Path, given: Path | None) -> Path:
    """The main repo of a lane git no longer knows: --repo, else <repo>-lanes/<name>."""
    if given is None:
        parent = path.parent
        if not parent.name.endswith("-lanes"):
            raise SystemExit(f"BLOCK: {path} is not a live git worktree and is not under a "
                             "<repo>-lanes directory - pass --repo")
        given = parent.parent / parent.name[:-len("-lanes")]
        if not (given / ".git").exists():
            raise SystemExit(f"BLOCK: {path} is not a live git worktree and {given} is not a "
                             "git repo - pass --repo")
    repo = repo_root(Path(given).resolve())
    if is_linked_worktree(repo):
        raise SystemExit(f"BLOCK: {repo} is itself a lane - pass the main repo as --repo")
    return repo


def _is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(os.lstat(path), "st_reparse_tag", 0) == getattr(
        stat, "IO_REPARSE_TAG_MOUNT_POINT", -1)


def remove_tree(root: Path) -> list[str]:
    """Delete a directory without ever following a link out of it.

    The lane's links are junctions into the main repo (venv, node_modules),
    and a leftover may still hold them, so a link is removed as one object
    and never descended into. Returns "path: reason" for what stayed.
    """
    failures: list[str] = []

    def remove(path: Path) -> bool:
        try:
            if _is_link(path):
                try:
                    os.rmdir(path)
                except OSError:
                    os.unlink(path)
                return True
            if path.is_dir():
                ok = all([remove(child) for child in path.iterdir()])
                if ok:
                    os.rmdir(path)
                return ok
            try:
                os.unlink(path)
            except PermissionError:
                os.chmod(path, stat.S_IWRITE)
                os.unlink(path)
            return True
        except OSError as exc:
            failures.append(f"{path}: {exc.strerror or exc}")
            return False

    remove(root)
    return failures


def clear_leftover(repo: Path, path: Path) -> int:
    """Delete what is left of a lane directory, then prune git's registration."""
    if os.path.lexists(path):
        failures = remove_tree(path)
        if failures:
            print(f"BLOCK: could not delete {len(failures)} path(s) under {path}; something "
                  "still holds them:")
            for line in failures[:10]:
                print(f"  ! {line}")
            print(f"Look for a process whose command line or working directory contains {path} "
                  "(PowerShell: Get-CimInstance Win32_Process | Where-Object CommandLine -like "
                  f"'*{path.name}*' | Select-Object ProcessId,Name,CommandLine), stop it or move "
                  "that shell out, then run this close again - it continues from here.")
            return 1
        print(f"deleted the leftover directory {path}")
    git(["worktree", "prune"], cwd=repo, check=False)
    if registered_worktree(repo, path):
        print(f"BLOCK: git still lists {path} as a worktree after prune - "
              f"check `git -C {repo} worktree list`")
        return 1
    print(f"pruned the worktree registration for {path}")
    return 0


def close_leftover(path: Path, given: Path | None, task_id: str | None,
                   no_untrust: bool) -> int:
    """Finish closing a lane (task_id set) or blind worktree that git no longer serves.

    Deletes only a directory directly under this repo's lanes root whose name
    is that lane's - anything else is left alone, whatever state it is in.
    """
    repo = leftover_repo(path, given)
    if os.path.lexists(path):
        if task_id is not None:
            former = path.name.casefold() == task_id.casefold()
        else:
            former = path.name.endswith("-blind") or (path / "verify" / ".blind.json").is_file()
        if path.parent != lanes_root(repo) or not former:
            raise SystemExit(f"BLOCK: {path} is not a live git worktree, and it is not a former "
                             f"{'lane ' + task_id if task_id else 'blind worktree'} directly under "
                             f"{lanes_root(repo)} - left alone; remove it by hand if it is debris")
        td = task_dir(path, task_id) if task_id else None
        archive = repo / ".delegate-runs" / "ARCHIVE" / task_id if task_id else None
        if td is not None and td.is_dir():
            if archive.exists():
                print(f"run directory already archived at {archive} - not copied again")
            else:
                try:
                    shutil.copytree(td, archive, symlinks=True)
                except (OSError, shutil.Error) as exc:
                    raise SystemExit(f"BLOCK: archiving the leftover run directory {td} failed, "
                                     f"so nothing was deleted: {exc}")
                print(f"archived the leftover run directory -> {archive}")
    elif registered_worktree(repo, path):
        print(f"{path} is already gone - nothing could be archived, and any commit it held "
              "is already unreferenced")
    else:
        print(f"{path} is gone and git does not list it - nothing to remove")
    if clear_leftover(repo, path):
        return 1
    verify_sources(repo, load_config(repo)["links"])
    if task_id is not None:
        drop_accept_scratch(repo / ".delegate-runs" / "ARCHIVE" / task_id, task_id)
    if task_id is not None and not no_untrust:
        untrust(path)
    return 0


def untrust(lane: Path) -> None:
    out = subprocess.run([sys.executable, str(DOCTOR), "--untrust", str(lane)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(out.stdout.strip() or out.stderr.strip())
    if out.returncode != 0:
        # Not fatal - the worktree is already gone - but say it, because trust
        # entries accumulate invisibly and nothing else will mention it again.
        print(f"WARNING: trust entry for {lane} was not removed (rc={out.returncode})")


def task_dir(lane: Path, task_id: str) -> Path:
    return lane / ".delegate-runs" / task_id


def read_ref(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"BLOCK: cannot read {what} at {path}: {exc}")


def build_prompt(mode: str, task_id: str, turn: int, recovery: bool) -> str:
    """PROMPT.txt = the worker contract, the per-mode note, the finding contract
    when this is an audit, then ONE instruction line naming this turn.

    The contract files are read from the plugin, never inlined here: a second
    copy of a contract is a copy that drifts, and the worker would then be held
    to a version nothing else in the plugin agrees with.
    """
    parts = [read_ref(SKILL_ROOT / "references" / "worker-contract.md", "worker-contract.md")]
    if mode == "research":
        parts.append(RESEARCH_NOTE)
    elif mode == "audit":
        parts.append(AUDIT_NOTE)
        parts.append(read_ref(AUDIT_ROOT / "references" / "finding-contract.md",
                              "finding-contract.md"))
    line = (RECOVERY_INSTRUCTION if recovery else INSTRUCTION)
    parts.append(line.format(task_id=task_id, turn=turn))
    return "\n\n".join(p.strip("\n") for p in parts) + "\n"


def seed_turn(lane: Path, task_id: str, turn: int, mode: str, recovery: bool) -> list[str]:
    td = task_dir(lane, task_id)
    td.mkdir(parents=True, exist_ok=True)
    made = []
    cl = td / f"turn-{turn}.md"
    if cl.exists() and SKELETON_MARKER not in cl.read_text(encoding="utf-8"):
        # A filled changelog is the record of a turn that happened. Overwriting
        # it would erase the only account of what the worker did.
        raise SystemExit(f"BLOCK: {cl} is already filled in - pick the next turn number")
    cl.write_text(SKELETON.format(task_id=task_id, turn=turn), encoding="utf-8")
    made.append(str(cl))
    # The previous turn's completion marker has to go NOW, not when dispatch
    # starts. Measured: `nohup dispatch.py &` then the documented poll returned
    # on its first iteration - python takes ~100ms to reach its own unlink, the
    # `[ -f ]` test takes ~1ms - so every retry from turn 2 on read the last
    # turn's rc, verdict and FINAL.txt as this turn's result. Seeding a turn is
    # the moment the old result stops being true.
    (td / "DONE").unlink(missing_ok=True)
    prompt = td / "PROMPT.txt"
    prompt.write_text(build_prompt(mode, task_id, turn, recovery), encoding="utf-8")
    made.append(str(prompt))
    return made


def lane_mode(lane: Path, task_id: str, given: str | None) -> str:
    """The lane's mode, remembered from `open` when `turn` is not told again.

    Measured in review: re-seeding an audit lane without repeating `--mode
    audit` rebuilt PROMPT.txt at 5324 bytes instead of 20177 - the finding
    contract, the probe rules and the green_when requirement all silently
    gone, and the failure would have surfaced two steps later as "the worker
    ignored the format". A mode that lives only in the architect's memory of
    the last command is a mode that changes between turns.
    """
    marker = task_dir(lane, task_id) / "lane.json"
    if given:
        return given
    stored = None
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
        stored = record.get("mode") if isinstance(record, dict) else None
    except (OSError, ValueError):
        stored = None
    if stored in MODES:
        return stored
    # A mode nobody here understands must stop the run, not quietly become
    # `worker`: measured, `{"mode": "Audit"}` (a capitalisation slip in a
    # hand-edited file) rebuilt an audit PROMPT.txt as a 5282-byte worker one
    # with no warning at all, and the loss only shows up as "the worker
    # ignored the finding format" two steps later.
    if stored is not None:
        raise SystemExit(f"BLOCK: {marker} records mode {stored!r}, which is not one of "
                         f"{sorted(MODES)} - fix it or pass --mode")
    raise SystemExit(f"BLOCK: no usable mode in {marker} and --mode not given. "
                     "Pass --mode worker|audit|research (a lane opened before this "
                     "field existed has no record of what it is).")


def spec_state(lane: Path, task_id: str) -> str:
    """"ok" | "missing" | "skeleton" - the gate on printing a dispatch line."""
    spec = task_dir(lane, task_id) / "SPEC.md"
    if not spec.is_file() or not spec.read_text(encoding="utf-8").strip():
        return "missing"
    if SKELETON_MARKER in spec.read_text(encoding="utf-8"):
        return "skeleton"
    return "ok"


def write_tool_bridges(td: Path, specs: list[str]) -> list[str]:
    """Write NAME.cmd/NAME.sh wrappers around executables that live outside the lane.

    The whole trick is where the absolute path sits. dispatch.py's approval
    filter is a lexical scan of the COMMAND LINE, so `bash -lc
    "C:/proj/venv/Scripts/python.exe -m pytest"` is declined as an out-of-lane
    operand - measured 5 Sep 2026, two turns, 0 files written. The same
    interpreter reached through .delegate-runs/<id>/py.cmd is approved, because
    the path is inside the script and the filter never reads file contents.

    This is a usability fix, not a hole: the worker could already reach any
    executable through an approved shell, and the sandbox - not this scan - is
    what actually contains writes. A venv interpreter is exactly the case the
    lane model cannot serve on its own, since a worktree gets no venv.
    """
    written: list[str] = []
    for spec in specs:
        name, sep, target = spec.partition("=")
        name = name.strip()
        target = target.strip()
        if not sep or not name or not target:
            raise SystemExit(f"BLOCK: --tool expects NAME=PATH, got {spec!r}")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise SystemExit(f"BLOCK: --tool name {name!r} must be letters, digits, "
                             "dot, dash or underscore - it becomes a filename")
        # absolute(), never resolve(): a venv's bin/python is a symlink to the
        # bare base interpreter, and following it dropped the venv's
        # site-packages from the bridge (measured: `No module named 'fastapi'`).
        resolved = Path(target).expanduser().absolute()
        if not resolved.exists():
            # A bridge to a missing executable fails inside the worker's turn,
            # where the error reads as the worker's fault. Fail here instead.
            raise SystemExit(f"BLOCK: --tool {name} points at {resolved}, which does not exist")

        # newline="" on both: these literals already carry the endings each
        # file needs, and write_text would translate them again - measured, the
        # .cmd came out CR CR LF, and the .sh would get CRLF, which /bin/sh
        # reads as part of the interpreter name on the shebang line.
        cmd_path = td / f"{name}.cmd"
        cmd_path.write_text(f'@echo off\r\n"{resolved}" %*\r\n',
                            encoding="utf-8", newline="")
        # POSIX form for the sh bridge: Git Bash is the shell Codex reaches on
        # Windows, and a backslash path inside double quotes is unusable there.
        sh_path = td / f"{name}.sh"
        sh_path.write_text(f'#!/bin/sh\nexec "{resolved.as_posix()}" "$@"\n',
                           encoding="utf-8", newline="")
        sh_path.chmod(sh_path.stat().st_mode | 0o111)
        written += [str(cmd_path), str(sh_path)]
    return written


ACCEPT_TMP_ROOT = "serit-accept"


def accept_scratch(task_id: str) -> Path:
    """The per-task temp dir for ACCEPT.py's children and the pytest bridge.

    Outside the lane on purpose. It lived in the lane's tmp/ first, and then
    every child's tmp_path sat inside a git worktree - and, for a Unity repo,
    inside a Unity project. Measured 11 Oct 2026 (lane
    2026-10-11-small-backend): the Backend suite with an in-lane basetemp
    failed 12 tests that need tmp_path outside any repository (10
    test_workspace_gitignore, 2 test_unity_file_guard); the OS temp dir passes
    them. The worker never names this path - it is written into ACCEPT.py and
    the bridge, which the approval filter does not read.
    """
    return Path(tempfile.gettempdir()).resolve() / ACCEPT_TMP_ROOT / task_id


def write_pytest_bridge(td: Path, py: Path | None, scratch: Path) -> list[str]:
    """pytest.cmd/pytest.sh: the lane's interpreter running pytest with no
    cache and a basetemp under `scratch`.

    The worker needs a way to give an extra pytest run a basetemp outside the
    repository, and the approval filter declines any command that names TEMP
    or an absolute path outside the lane. The path inside this script is never
    scanned (see write_tool_bridges). pytest creates only the basetemp itself,
    not its parents, so the script creates `scratch` first.
    """
    cmd_py = f'"{py}"' if py else "python"
    sh_py = f'"{py.as_posix()}"' if py else "python3"
    cmd_path = td / "pytest.cmd"
    cmd_path.write_text(
        "@echo off\r\nsetlocal\r\nset PYTHONDONTWRITEBYTECODE=1\r\n"
        f'if not exist "{scratch}" mkdir "{scratch}"\r\n'
        f'{cmd_py} -m pytest -p no:cacheprovider "--basetemp={scratch / "pytest"}" %*\r\n'
        "exit /b %ERRORLEVEL%\r\n", encoding="utf-8", newline="")
    sh_path = td / "pytest.sh"
    sh_path.write_text(
        "#!/bin/sh\nexport PYTHONDONTWRITEBYTECODE=1\n"
        f'mkdir -p "{scratch.as_posix()}"\n'
        f'exec {sh_py} -m pytest -p no:cacheprovider "--basetemp={(scratch / "pytest").as_posix()}" "$@"\n',
        encoding="utf-8", newline="")
    sh_path.chmod(sh_path.stat().st_mode | 0o111)
    return [str(cmd_path), str(sh_path)]


def drop_accept_scratch(td: Path | None, task_id: str) -> None:
    """Remove the lane's outside-the-repo ACCEPT temp dir, if it is still there.

    The path is recomputed from the task id, never read from lane.json: the
    worker can write lane.json, and a security review (11 Oct 2026) showed a
    name-only check let a forged record aim close's rmtree at any
    `serit-accept/<task-id>` directory on the machine.
    """
    path = accept_scratch(task_id)
    if not path.exists():
        return
    shutil.rmtree(path, ignore_errors=True)
    print(f"{'WARNING: could not remove' if path.exists() else 'removed'} acceptance temp dir {path}")


CONFIG_NAME = ".delegate-lane.toml"

ACCEPT_TEMPLATE = r"""#!/usr/bin/env python3
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

MARKER = __MARKER__
TOOLS = __TOOLS__
COMMANDS = __COMMANDS__
SCRATCH = __SCRATCH__
PORTS = __PORTS__

# A cp1252 console cannot print Turkish probe output: measured 12 Oct 2026,
# ACCEPT.py died with UnicodeEncodeError on a probe's "ı", and the worker
# cannot set PYTHONIOENCODING itself (the approval filter declines it), so it
# stopped. The children get the same through child_env.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

task = Path(__file__).resolve().parent
lane = task.parents[1]
failures = []

# Every child gets its temp dirs from here. Workers used to set TEMP/TMP
# themselves in a shell wrapper, and the dispatcher's approval filter declines
# any command naming those variables - measured 10-11 Oct 2026, 3 of 3
# Python-tested turns ended BLOCKED-BY-APPROVALS with acceptance never run.
# A per-task dir under the OS temp dir, not the lane: inside the worktree,
# tests that need tmp_path outside any git repository or Unity project failed
# (12 in one Backend suite, measured 11 Oct 2026). Removed when acceptance
# passes, kept for a look when not; `close` removes what is left.
scratch = Path(SCRATCH)
shutil.rmtree(scratch, ignore_errors=True)
scratch.mkdir(parents=True, exist_ok=True)


def child_env(**extra):
    env = dict(os.environ, **extra)
    for key in ("TEMP", "TMP", "TMPDIR"):
        env[key] = str(scratch)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if PORTS:
        # This lane's own port range: two lanes whose probes both used 8026
        # failed each other (measured 12 Oct 2026). Probes may ignore it.
        env["PROBE_PORT_BASE"], env["PROBE_PORT_COUNT"] = str(PORTS[0]), str(PORTS[1])
    # pytest splits PYTEST_ADDOPTS POSIX-style, where a backslash is an escape:
    # the path goes in with forward slashes, quoted in case it has spaces.
    opts = "-p no:cacheprovider --basetemp=" + shlex.quote((scratch / "pytest").as_posix())
    env["PYTEST_ADDOPTS"] = (env.get("PYTEST_ADDOPTS", "") + " " + opts).strip()
    return env


CRASH = re.compile(r"^Traceback \(most recent call last\):", re.M)


def crashed(output):
    # An uncaught exception other than an assert is the probe breaking, not a
    # verdict on the code; say so, so the worker records it instead of stopping.
    lines = [ln for ln in output.splitlines() if ln.strip()]
    return bool(CRASH.search(output)) and bool(lines) and not lines[-1].startswith("AssertionError")


def report(name, ok, output=""):
    if not ok and crashed(output):
        name += " (uncaught exception in the probe itself, not a verdict - record it in the changelog)"
    print(("ok   " if ok else "FAIL ") + name)
    tail = [ln for ln in output.splitlines() if ln.strip()][-(20 if not ok else 5):]
    for ln in tail:
        print("       " + ln)
    if not ok:
        failures.append(name)


def bridge(name):
    path = task / (name + (".cmd" if os.name == "nt" else ".sh"))
    return str(path) if path.is_file() else None


def run(argv, env=None):
    try:
        r = subprocess.run(argv, cwd=lane, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return r.returncode, r.stdout + r.stderr


turns = [(int(m.group(1)), p) for p in task.glob("turn-*.md")
         if (m := re.fullmatch(r"turn-(\d+)\.md", p.name))]
if not turns:
    report("changelog turn-N.md exists", False, f"no turn-N.md in {task}")
    sys.exit(1)
number, changelog = max(turns)
body = changelog.read_text(encoding="utf-8", errors="replace")
if not body.strip() or MARKER in body:
    report(f"changelog {changelog.name} is filled in", False,
           "missing, empty, or still the seeded skeleton")
    sys.exit(1)
report(f"changelog {changelog.name} is filled in", True)

env = child_env(AUDIT_ROOT=str(lane))
probes = task / "probes"
py = bridge("py")
for probe in sorted(probes.iterdir()) if probes.is_dir() else []:
    if probe.suffix == ".py":
        argv = [py or sys.executable, str(probe)]
    elif probe.suffix in (".cjs", ".js"):
        argv = ["node", str(probe)]
    else:
        continue
    rc, out = run(argv, env=env)
    report(f"probe {probe.name}", rc == 0, out)

for argv in COMMANDS:
    argv = list(argv)
    if argv and argv[0] in TOOLS and bridge(argv[0]):
        argv[0] = bridge(argv[0])
    rc, out = run(argv, env=child_env())
    report("command " + " ".join(argv), rc == 0, out)

if failures:
    print(f"FAILED: {len(failures)}")
    sys.exit(1)
shutil.rmtree(scratch, ignore_errors=True)
print("ACCEPT ALL OK")
"""


def load_config(repo: Path) -> dict:
    """The per-repo lane config, validated; an absent file changes nothing.

    Measured in a six-lane field session: every lane in a Python+Node project
    took 5-6 hand commands after `open` (link the venv and node_modules, point
    the interpreter bridge at the venv, copy probes, write the acceptance
    runner). They are the same commands each time, so they live in the repo.
    """
    path = repo / CONFIG_NAME
    cfg = {"links": [], "tools": {}, "commands": [], "writable": [], "ports": None,
           "lint_timeout": LINT_TIMEOUT}
    if not path.is_file():
        return cfg
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"BLOCK: cannot read {path}: {exc}")
    links = raw.get("links", [])
    if not isinstance(links, list) or not all(isinstance(x, str) and x for x in links):
        raise SystemExit(f"BLOCK: {path}: links must be a list of repo-relative path strings")
    for rel in links:
        parts = Path(rel).parts
        if Path(rel).is_absolute() or ".." in parts or not parts:
            raise SystemExit(f"BLOCK: {path}: link {rel!r} must be a path inside the repo")
    tools = raw.get("tools", {})
    # A venv interpreter lives at Scripts/python.exe on Windows and bin/python
    # elsewhere, and the same config is shared by both machines through git,
    # so a tool may name one path per platform.
    platform = "windows" if os.name == "nt" else "posix"
    if not isinstance(tools, dict):
        raise SystemExit(f"BLOCK: {path}: [tools] must map NAME to a repo-relative path string")
    resolved = {}
    for name, value in tools.items():
        if isinstance(value, dict) and value and set(value) <= {"windows", "posix"} and all(
                isinstance(v, str) and v for v in value.values()):
            value = value.get(platform)
            if value is None:
                continue
        elif not (isinstance(value, str) and value):
            raise SystemExit(f"BLOCK: {path}: [tools] {name} must be a repo-relative path string "
                             "or a table with `windows` and/or `posix` path strings")
        resolved[name] = value
    tools = resolved
    accept = raw.get("accept", {})
    commands = accept.get("commands", []) if isinstance(accept, dict) else None
    if (commands is None or not isinstance(commands, list)
            or not all(isinstance(c, list) and c and all(isinstance(a, str) for a in c)
                       for c in commands)):
        raise SystemExit(f"BLOCK: {path}: [accept] commands must be a list of argv lists")
    # Runtime output the code writes on its own (an app logger creating logs/
    # on import). serit adds these globs to every FILE WHITELIST and keeps them
    # out of DO-NOT-TOUCH: measured 12 Oct 2026, a verify lane whose spec
    # forbade everything outside the task dir stopped on the app's own logs/.
    writable = raw.get("writable", [])
    if not isinstance(writable, list) or not all(isinstance(x, str) and x.strip() for x in writable):
        raise SystemExit(f"BLOCK: {path}: writable must be a list of repo-relative glob strings")
    for rel in writable:
        parts = Path(rel).parts
        if Path(rel).is_absolute() or ".." in parts or rel.startswith(("/", "\\")):
            raise SystemExit(f"BLOCK: {path}: writable {rel!r} must be a glob inside the repo")
    ports = raw.get("ports")
    if ports is not None:
        ok = (isinstance(ports, dict) and set(ports) <= {"base", "per_lane"}
              and all(isinstance(ports.get(k), int) and not isinstance(ports.get(k), bool)
                      for k in ("base", "per_lane")))
        if not ok or not (1024 <= ports["base"] <= 65000) or not (1 <= ports["per_lane"] <= 1000):
            raise SystemExit(f"BLOCK: {path}: ports must be {{base = <1024-65000>, per_lane = <1-1000>}}")
        ports = (ports["base"], ports["per_lane"])
    lint = raw.get("lint", {})
    timeout = lint.get("timeout", LINT_TIMEOUT) if isinstance(lint, dict) else None
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 1 <= timeout <= 600:
        raise SystemExit(f"BLOCK: {path}: [lint] timeout must be a number of seconds, 1-600")
    cfg.update(links=links, tools=tools, commands=commands, writable=writable, ports=ports,
               lint_timeout=timeout)
    return cfg


PORT_SLOTS = 64


def port_records(repo: Path) -> list[tuple[Path, dict]]:
    """Every lane.json and blind .blind.json under this repo's lanes root."""
    root = lanes_root(repo)
    found = []
    if not root.is_dir():
        return found
    for path in [*root.glob("*/.delegate-runs/*/lane.json"), *root.glob("*/verify/.blind.json")]:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict):
            found.append((path, record))
    return found


def allocate_ports(repo: Path, cfg: dict) -> tuple[int, int] | None:
    """The lowest free `per_lane`-wide range from `base` among the open lanes.

    Two lanes whose probes both served on 8026 at the same time failed each
    other's acceptance (measured 12 Oct 2026). A range recorded by an open lane
    or blind worktree stays taken until it is closed; ranges handed out by hand
    (a SPEC that says "8020-8029") are not seen, so start `base` above them.
    """
    if not cfg.get("ports"):
        return None
    base, width = cfg["ports"]
    taken = {r.get("port_base") for _, r in port_records(repo) if isinstance(r.get("port_base"), int)}
    for slot in range(PORT_SLOTS):
        start = base + slot * width
        if start + width - 1 > 65535:
            break
        if start not in taken:
            return start, width
    raise SystemExit(f"BLOCK: no free probe port range: {PORT_SLOTS} ranges from {base} are all held "
                     "by open lanes - close some first")


DELETES_HEADING = "ARCHITECT DELETES"


def spec_text(lane: Path, task_id: str) -> str:
    spec = task_dir(lane, task_id) / "SPEC.md"
    try:
        return spec.read_text(encoding="utf-8")
    except OSError:
        return ""


def spec_section_lines(text: str, heading: str) -> list[str]:
    """Bullet values under `## <heading>`, backticks stripped."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("#"):
            inside = line.lstrip("#").strip().upper() == heading
            continue
        item = line.strip()
        if inside and item.startswith(("- ", "* ")):
            value = item[2:].split(" #", 1)[0].strip().strip("`'\"")
            if value:
                out.append(value)
    return out


def tracked(lane: Path) -> set[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=str(lane), capture_output=True)
    return {p.decode("utf-8", errors="replace") for p in out.stdout.split(b"\0") if p}


def apply_architect_deletes(lane: Path, task_id: str) -> list[str]:
    """`git rm` each tracked path listed under SPEC.md's ## ARCHITECT DELETES.

    Codex's sandbox cannot delete a file (apply_patch: "Failed to delete
    file"), so a probe that needs a file gone burned two turns on 12 Oct 2026.
    The architect lists the paths and this does the deleting before dispatch.
    Only tracked files inside the lane, never a link, never outside it.
    """
    lines = []
    paths = spec_section_lines(spec_text(lane, task_id), DELETES_HEADING)
    if not paths:
        return lines
    known = tracked(lane)
    for raw in paths:
        rel = raw.replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        rel = rel.rstrip("/")
        target = lane / rel
        if Path(rel).is_absolute() or ".." in Path(rel).parts or not _inside_real(target, lane):
            lines.append(f"ARCHITECT DELETES: {raw!r} is not a path inside the lane - left alone")
            continue
        if rel.startswith(".delegate-runs/") or rel == ".delegate-runs":
            lines.append(f"ARCHITECT DELETES: {raw!r} is in the run directory - left alone")
            continue
        is_dir = any(p.startswith(rel + "/") for p in known)
        if rel not in known and not is_dir:
            state = "already deleted" if not os.path.lexists(target) else "not a tracked file, left alone"
            lines.append(f"ARCHITECT DELETES: {rel} {state}")
            continue
        out = subprocess.run(["git", "rm", "-q", *(["-r"] if is_dir else []), "--", rel],
                             cwd=str(lane), capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
        lines.append(f"ARCHITECT DELETES: {'deleted ' + rel if out.returncode == 0 else 'FAILED ' + rel + ': ' + out.stderr.strip()[:200]}")
    return lines


LINT_TIMEOUT = 20
NO_DELETION = re.compile(r"\bno file deletion\b", re.I)
# A negated existence test on one line: Python and Node spellings.
GONE_CHECK = re.compile(
    r"not\s+[^\n]*\b(?:exists|is_file|is_dir|isfile|isdir|lexists)\s*\("
    r"|\b(?:exists|is_file|is_dir|isfile|isdir|lexists)\s*\([^\n]*\)\s*(?:is\s+False|==\s*False)"
    r"|!\s*(?:fs\.)?existsSync\s*\("
    r"|\b(?:existsSync|exists|is_file)\s*\([^\n]*\)\s*(?:===?\s*false)", re.I)
LITERAL = re.compile(r"""(['"])([^'"\n]{2,200})\1""")
PY_CRASH = re.compile(r"^Traceback \(most recent call last\):", re.M)
NODE_CRASH = re.compile(r"^\s+at .+[:(]\d+:\d+\)?\s*$", re.M)


def gone_expectations(source: str, known: set[str]) -> list[str]:
    """Tracked paths a probe line asserts do NOT exist (heuristic, line-based)."""
    hits = []
    folded = {p.casefold(): p for p in known}
    dirs = {}
    for p in known:
        parts = p.split("/")
        for i in range(1, len(parts)):
            dirs.setdefault("/".join(parts[:i]).casefold(), "/".join(parts[:i]))
    for line in source.splitlines():
        if not GONE_CHECK.search(line):
            continue
        literals = [m.group(2).replace("\\", "/") for m in LITERAL.finditer(line)]
        # `ROOT / "a" / "b.py"` spells one path in pieces; join neighbours too.
        candidates = set(literals)
        for i in range(len(literals)):
            for j in range(i + 1, len(literals) + 1):
                candidates.add("/".join(literals[i:j]))
        keys = {cand.strip("/").removeprefix("./").casefold() for cand in candidates}
        for key in keys:
            if key in folded:
                hits.append(folded[key])
            elif key in dirs and not any(o.startswith(key + "/") for o in keys):
                # "src" alone is a piece of "src"/"old.py", not a claim that src/ is gone.
                hits.append(dirs[key] + "/")
    return sorted(set(hits))


def kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            pass
    try:
        proc.communicate(timeout=10)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass


def probe_crash(output: str) -> str | None:
    """The exception line when a probe died of an uncaught exception, else None.

    An assert is the probe's verdict and counts as failing for the right reason;
    anything else (a JSONDecodeError on a binary body, a KeyError, a
    FileNotFoundError) is the probe breaking before it reached a verdict.
    """
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if PY_CRASH.search(output):
        tail = lines[-1] if lines else ""
        return None if tail.startswith("AssertionError") else tail[:200]
    if NODE_CRASH.search(output) and not re.search(r"AssertionError", output):
        thrown = next((ln for ln in lines if re.match(r"^[A-Za-z]*Error\b", ln)), lines[0] if lines else "")
        return thrown[:200]
    return None


def lint_lane(lane: Path, task_id: str, timeout: float) -> tuple[list[str], list[str]]:
    """(problems, notes) for the probes of a lane about to be dispatched.

    (a) a probe asserting a tracked path is gone while SPEC.md forbids file
        deletion - Codex cannot delete files, so the turn stops (2 turns lost,
        12 Oct 2026);
    (b) a probe that dies of an uncaught exception at base instead of failing
        with a verdict - the worker then stops on the probe's bug.
    Probes over `timeout` are killed (with their process tree) and reported as
    skipped. Note what (b) cannot see: a crash on a code path the base never
    reaches only shows up after the change.
    """
    td = task_dir(lane, task_id)
    text = spec_text(lane, task_id)
    probes = [p for d in ("probes", "prior-probes") if (td / d).is_dir()
              for p in sorted((td / d).iterdir()) if p.suffix in (".py", ".js", ".cjs")]
    problems, notes = [], []
    if NO_DELETION.search(text):
        known = tracked(lane)
        listed = {p.replace("\\", "/").strip("/") for p in spec_section_lines(text, DELETES_HEADING)}
        for probe in probes:
            try:
                source = probe.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            gone = [p for p in gone_expectations(source, known) if p.strip("/") not in listed]
            if gone:
                problems.append(
                    f"{probe.parent.name}/{probe.name} expects tracked path(s) to be gone - "
                    f"{', '.join(gone)} - but SPEC.md says no file deletion, and Codex cannot delete "
                    f"files anyway. List them under `## {DELETES_HEADING}` in SPEC.md (open/turn/lint "
                    "then `git rm` them in the lane) or delete them in the lane yourself before dispatch.")
    record = read_record(td)
    py = td / ("py.cmd" if os.name == "nt" else "py.sh")
    scratch = accept_scratch(task_id).parent / (task_id + "-lint")
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, AUDIT_ROOT=str(lane), TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch),
               PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    if isinstance(record.get("port_base"), int) and isinstance(record.get("port_count"), int):
        env["PROBE_PORT_BASE"], env["PROBE_PORT_COUNT"] = str(record["port_base"]), str(record["port_count"])
    try:
        for probe in probes:
            argv = ([str(py) if py.is_file() else sys.executable, str(probe)] if probe.suffix == ".py"
                    else ["node", str(probe)])
            kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                      else {"start_new_session": True})
            name = f"{probe.parent.name}/{probe.name}"
            try:
                proc = subprocess.Popen(argv, cwd=str(lane), env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
            except OSError as exc:
                notes.append(f"skipped {name}: cannot start ({exc})")
                continue
            try:
                raw, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                kill_tree(proc)
                notes.append(f"skipped {name}: still running after {timeout:g}s (killed with its children)")
                continue
            out = raw.decode("utf-8", errors="replace")
            crash = probe_crash(out) if proc.returncode != 0 else None
            if crash:
                problems.append(f"{name} crashed at base with an uncaught exception instead of failing "
                                f"with a verdict: {crash}. Catch it in the probe and print a FAIL line.")
            else:
                notes.append(f"{name}: rc={proc.returncode} at base"
                             + (" (passes before any change)" if proc.returncode == 0
                                and probe.parent.name == "probes" and record.get("mode") == "worker" else ""))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return problems, notes


def report_lint(problems: list[str], notes: list[str]) -> None:
    for line in notes:
        print(f"lint: {line}")
    if problems:
        print("LINT PROBLEMS - fix these before dispatch:")
        for line in problems:
            print(f"  ! {line}")


def merged_tools(repo: Path, cfg: dict, cli: list[str]) -> list[str]:
    """Config tools as NAME=<repo>/<path> specs; a --tool of the same name wins."""
    named = {spec.partition("=")[0].strip() for spec in cli}
    from_cfg = [f"{name}={repo / rel}" for name, rel in cfg["tools"].items()
                if name not in named]
    return from_cfg + list(cli)


def is_junction(path: Path) -> bool:
    return os.name == "nt" and path.is_dir() and not path.is_symlink() and bool(
        getattr(os.path, "isjunction", lambda p: False)(path))


def make_link(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt" and source.is_dir():
        # A directory symlink needs a privilege most Windows accounts lack; a
        # junction does not, and for a venv or node_modules it is equivalent.
        out = subprocess.run(["cmd", "/c", "mklink", "/J", str(dest), str(source)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        if out.returncode != 0:
            raise SystemExit(f"BLOCK: mklink /J {dest} -> {source} failed: "
                             f"{(out.stdout + out.stderr).strip()}")
    else:
        os.symlink(source, dest, target_is_directory=source.is_dir())


def exclude_file(lane: Path) -> Path | None:
    """The exclude file git actually reads for this worktree.

    Measured: an entry written to the worktree's own git dir (`rev-parse
    --git-dir`/info/exclude) is ignored by git - linked worktrees read the
    SHARED `info/exclude`, and `?? .venv` stayed in `git status`. So the entry
    goes in the shared file, wrapped in a block keyed by the lane path that
    close removes again; two lanes linking the same path each own a copy.
    """
    out = git(["rev-parse", "--git-path", "info/exclude"], cwd=lane, check=False)
    if not out:
        return None
    path = Path(out)
    return path if path.is_absolute() else lane / path


def drop_exclude_block(lane: Path) -> None:
    path = exclude_file(lane)
    if path is None or not path.is_file():
        return
    begin, end = f"# delegate-lane begin {lane}", f"# delegate-lane end {lane}"
    kept, skipping = [], False
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        if line.rstrip("\r\n") == begin:
            skipping = True
        elif skipping and line.rstrip("\r\n") == end:
            skipping = False
        elif not skipping:
            kept.append(line)
    path.write_text("".join(kept), encoding="utf-8", newline="")


def _inside_real(path: Path, root: Path) -> bool:
    """Is path, after resolving every symlink, inside root's real path?

    Measured: link_into only rejected absolute and `..` config paths, so a repo
    path that is itself a symlink to outside the repo was linked into the lane
    and handed the worker a writable door to that directory.
    """
    real, base = os.path.realpath(path), os.path.realpath(root)
    try:
        return os.path.commonpath([os.path.normcase(real), os.path.normcase(base)]) == os.path.normcase(base)
    except ValueError:                              # different drives
        return False


def remaining_links(lane: Path, rels: list[str]) -> list[str]:
    """Recorded link paths that still exist after unlink_links ran."""
    return [rel for rel in rels if os.path.lexists(lane / rel)]


def link_block(lane: Path, left: list[str]) -> int:
    print(f"BLOCK: could not remove {len(left)} link(s) from {lane}; the worktree was NOT "
          "removed, because removing it could walk through a link into the real target:")
    for rel in left:
        print(f"  ! {lane / rel}")
    print("Fix the cause (permissions, a lock), then run close again - the lane is still usable.")
    return 1


def lane_fingerprint(lane: Path) -> str:
    """Digest of the lane's uncommitted work outside .delegate-runs.

    integrate copies the lane's changes into the main tree and leaves the lane
    as it was; close compares this digest with the one integrate recorded, so
    changes made after integrating are still caught.
    """
    exclude = [".", ":(exclude).delegate-runs"]
    h = hashlib.sha256()
    tracked = subprocess.run(["git", "diff", "HEAD", "--binary", "--", *exclude], cwd=str(lane),
                             capture_output=True).stdout
    h.update(tracked)
    others = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z",
                             "--", *exclude], cwd=str(lane), capture_output=True).stdout
    for raw in sorted(x for x in others.split(b"\0") if x):
        rel = raw.decode("utf-8", errors="replace")
        h.update(raw + b"\0")
        path = lane / rel
        if path.is_file() and not path.is_symlink():
            h.update(_digest(path).encode())
        elif path.is_symlink():
            h.update(os.readlink(path).encode())
    return h.hexdigest()


def read_record(td: Path) -> dict:
    try:
        record = json.loads((td / "lane.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def link_into(repo: Path, lane: Path, rels: list[str]) -> list[str]:
    """Link each repo-relative path into the lane and hide it from `git status`.

    Entries are anchored and carry no trailing slash - `.venv/` in a
    .gitignore does not match a symlink.
    """
    made: list[str] = []
    try:
        for rel in rels:
            source = repo / rel
            if not source.exists():
                raise SystemExit(f"BLOCK: {CONFIG_NAME} links {rel!r}, which does not exist in {repo}")
            if not _inside_real(source, repo):
                raise SystemExit(f"BLOCK: {CONFIG_NAME} links {rel!r}, which resolves to "
                                 f"{os.path.realpath(source)} - outside {repo}. A link "
                                 "must lead somewhere inside the repo")
            dest = lane / rel
            if os.path.lexists(dest):
                raise SystemExit(f"BLOCK: cannot link {rel!r}: {dest} already exists in the lane")
            make_link(source, dest)
            made.append(rel)
        exclude = exclude_file(lane) if made else None
        if exclude is not None:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("a", encoding="utf-8", newline="") as fh:
                fh.write(f"# delegate-lane begin {lane}\n"
                         + "".join(f"/{rel}\n" for rel in made)
                         + f"# delegate-lane end {lane}\n")
    except BaseException:
        unlink_links(lane, made)
        raise
    return made


def unlink_links(lane: Path, rels: list[str]) -> None:
    """Remove the links themselves - never anything behind them.

    `git worktree remove --force` and rmtree both risk walking through a
    Windows junction into the real venv, so links go first, one object each:
    os.unlink for a symlink, os.rmdir for a junction (which refuses a real
    non-empty directory).
    """
    drop_exclude_block(lane)
    for rel in rels:
        dest = lane / rel
        try:
            if dest.is_symlink():
                os.unlink(dest)
            elif is_junction(dest):
                os.rmdir(dest)
            elif os.path.lexists(dest):
                print(f"WARNING: {dest} is not a link, left alone", file=sys.stderr)
        except OSError as exc:
            print(f"WARNING: could not remove link {dest}: {exc}", file=sys.stderr)


def verify_sources(repo: Path, rels: list[str]) -> None:
    for rel in rels:
        if not (repo / rel).exists():
            print(f"ERROR: {repo / rel} no longer exists after removing its lane link - "
                  "a link removal went through to the target; restore it NOW", file=sys.stderr)


def copy_probes(src: Path, dest: Path, new_id: str) -> list[str]:
    """Copy a lane's probes, retargeting the task id embedded in their tmp paths."""
    src = Path(src)
    if not src.is_dir():
        raise SystemExit(f"BLOCK: --probes-from {src} is not a directory")
    old_id = None
    try:
        record = json.loads((src.parent / "lane.json").read_text(encoding="utf-8"))
        old_id = record.get("task_id") if isinstance(record, dict) else None
    except (OSError, ValueError):
        old_id = None
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for f in sorted(src.iterdir()):
        if not f.is_file():
            continue
        data = f.read_bytes()
        if isinstance(old_id, str) and old_id:
            try:
                data = data.decode("utf-8").replace(old_id, new_id).encode("utf-8")
            except UnicodeDecodeError:
                pass
        (dest / f.name).write_bytes(data)
        copied.append(str(dest / f.name))
    return copied


def write_accept(td: Path, cfg: dict, tool_names: list[str], scratch: Path | None = None,
                 ports: tuple[int, int] | None = None) -> str:
    scratch = scratch or accept_scratch(td.name)
    text = (ACCEPT_TEMPLATE.replace("__MARKER__", json.dumps(SKELETON_MARKER))
            .replace("__TOOLS__", json.dumps(tool_names))
            .replace("__COMMANDS__", json.dumps(cfg["commands"]))
            .replace("__SCRATCH__", json.dumps(str(scratch)))
            .replace("__PORTS__", repr(list(ports)) if ports else "None"))
    path = td / "ACCEPT.py"
    path.write_text(text, encoding="utf-8", newline="")
    return str(path)


def cmd_open(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.task_id):
        # The id becomes a directory name, part of every printed command and a
        # grep target. Refusing the awkward ones here is cheaper than quoting
        # them correctly in six places.
        raise SystemExit(f"BLOCK: --task-id {args.task_id!r} must be letters, digits, "
                         "dot, dash or underscore")
    repo = repo_root(args.repo)
    if is_linked_worktree(repo):
        raise SystemExit(f"BLOCK: {repo} is itself a lane - open lanes from the main tree "
                         "(a lane of a lane pins to the wrong base and archives nowhere)")
    base = git(["rev-parse", args.base], cwd=repo)
    lane = args.lane or (repo.parent / f"{repo.name}-lanes" / args.task_id)
    lane = Path(lane).resolve()
    if lane.exists():
        raise SystemExit(f"BLOCK: {lane} already exists - close it first, or pass --lane")

    cfg = load_config(repo)
    tool_specs = merged_tools(repo, cfg, args.tool)
    links: list[str] = []
    git(["worktree", "add", "--detach", str(lane), base], cwd=repo)
    try:
        # Verify rather than assume: a worktree branched from a stale base is
        # silent, and the whole audit then measures code nobody is running.
        head = git(["rev-parse", "HEAD"], cwd=lane)
        if head != base:
            raise SystemExit(f"BLOCK: lane HEAD {head} != requested base {base}")

        td = task_dir(lane, args.task_id)
        td.mkdir(parents=True, exist_ok=True)
        created = [str(td)]
        if args.mode in ("audit", "research"):
            # Only these modes get findings/ and probes/. On an implementation lane
            # they are unwhitelisted paths, so they would surface as a scope
            # violation in the footprint check the moment the worker wrote in them.
            for sub in ("findings", "probes"):
                (td / sub).mkdir(exist_ok=True)
                created.append(str(td / sub))
        if args.mode == "audit":
            shutil.copyfile(AUDIT_ROOT / "references" / "finding-contract.md",
                            td / "finding-contract.md")
            created.append(str(td / "finding-contract.md"))
        if args.spec:
            shutil.copyfile(args.spec, td / "SPEC.md")
            created.append(str(td / "SPEC.md"))
        if tool_specs:
            created += write_tool_bridges(td, tool_specs)
        scratch = accept_scratch(args.task_id)
        named = {spec.partition("=")[0].strip(): spec.partition("=")[2].strip() for spec in tool_specs}
        if "pytest" not in named:
            # A configured tool named pytest is the repo's own choice and wins.
            py = Path(named["py"]).expanduser().absolute() if named.get("py") else None
            created += write_pytest_bridge(td, py, scratch)
        links = link_into(repo, lane, cfg["links"])
        created += [str(lane / rel) for rel in links]
        if args.probes_from:
            (td / "probes").mkdir(exist_ok=True)
            created += copy_probes(args.probes_from, td / "probes", args.task_id)
        (td / "tmp").mkdir(exist_ok=True)
        created.append(str(td / "tmp"))
        ports = allocate_ports(repo, cfg)
        if args.accept:
            names = [spec.partition("=")[0].strip() for spec in tool_specs]
            created.append(write_accept(td, cfg, names, scratch, ports))

        # Written before the seeding so `turn` can find it even if the run dies
        # halfway: it is what stops a later turn from rebuilding an audit
        # PROMPT.txt as a worker one.
        record = {"task_id": args.task_id, "mode": args.mode, "base_sha": base,
                  "links": links, "accept_tmp": str(scratch)}
        if ports:
            record.update(port_base=ports[0], port_count=ports[1])
        (td / "lane.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        created.append(str(td / "lane.json"))

        created += seed_turn(lane, args.task_id, args.turn, args.mode, recovery=False)
        deleted = apply_architect_deletes(lane, args.task_id)

        if not args.no_trust:
            out = subprocess.run([sys.executable, str(DOCTOR), "--trust", str(lane)],
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace")
            print(out.stdout.strip() or out.stderr.strip())
            if out.returncode != 0:
                raise SystemExit(f"BLOCK: doctor.py --trust failed (rc={out.returncode}) - "
                                 "the worker cannot run in an untrusted path")
    except BaseException:
        # Anything from here on leaves a registered worktree behind, and the
        # next attempt then dies on "already exists" with a half-seeded task
        # dir inside it. The step that creates owns the step that removes.
        unlink_links(lane, links)
        git(["worktree", "remove", "--force", str(lane)], cwd=repo, check=False)
        git(["worktree", "prune"], cwd=repo, check=False)
        # Verify the rollback instead of announcing it. `check=False` means the
        # removal can fail silently - a file lock on Windows is the ordinary
        # way - and a rollback that only claims to have happened leaves the
        # next `open` blocked by debris the message said was gone.
        if lane.exists() or registered_worktree(repo, lane):
            print(f"ROLLBACK INCOMPLETE: {lane} is still there - remove it by hand "
                  f"(git -C {repo} worktree remove --force {lane})", file=sys.stderr)
        else:
            print(f"rolled back: removed the half-built lane at {lane}", file=sys.stderr)
        raise

    print(f"lane:     {lane}")
    print(f"base sha: {base}  (verified: lane HEAD matches)")
    print(f"task dir: {td}")
    for path in created:
        print(f"  + {path}")
    if ports:
        print(f"probe ports: {ports[0]}-{ports[0] + ports[1] - 1} (PROBE_PORT_BASE={ports[0]})")
    for line in deleted:
        print(line)
    print()
    print_next_steps(lane, args.task_id, args.turn, lint=not args.no_lint,
                     override=args.lint_override, timeout=args.lint_timeout or cfg["lint_timeout"])
    return 0


def cmd_turn(args: argparse.Namespace) -> int:
    lane = Path(args.lane).resolve()
    if not (lane / ".git").exists():
        raise SystemExit(f"BLOCK: {lane} is not a worktree")
    td = task_dir(lane, args.task_id)
    if not td.is_dir():
        # Otherwise a mistyped --task-id silently creates a second, empty run
        # directory and seeds a turn into a lane nothing will ever dispatch.
        raise SystemExit(f"BLOCK: no run directory at {td} - check --task-id")
    prev = td / f"turn-{args.turn - 1}.md"
    if args.turn > 1 and not prev.is_file():
        # The previous turn's changelog is the evidence that turn happened.
        # Its absence means either the turn was never run or something deleted
        # the record; either way the counter is wrong.
        raise SystemExit(f"BLOCK: {prev} is missing - is {args.turn} really the next turn?")
    mode = lane_mode(lane, args.task_id, args.mode)
    for path in seed_turn(lane, args.task_id, args.turn, mode, args.recovery):
        print(f"  ~ {path}")
    for line in apply_architect_deletes(lane, args.task_id):
        print(line)
    print()
    print_next_steps(lane, args.task_id, args.turn, lint=not args.no_lint, override=args.lint_override,
                     timeout=args.lint_timeout or load_config(main_repo_of(lane))["lint_timeout"])
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    """Re-run the pre-dispatch lint; print the dispatch line when it is clean.

    The way back after a lint block: fix the probe or SPEC.md (or list the
    files under ARCHITECT DELETES), run this, and it prints the dispatch line;
    --override prints it despite the problems. --report-only never prints it
    (serit uses that and prints its own).
    """
    lane = Path(args.lane).resolve()
    td = task_dir(lane, args.task_id)
    if not td.is_dir():
        raise SystemExit(f"BLOCK: no run directory at {td} - check --task-id")
    for line in apply_architect_deletes(lane, args.task_id):
        print(line)
    timeout = args.lint_timeout or load_config(main_repo_of(lane))["lint_timeout"]
    if args.report_only:
        problems, notes = lint_lane(lane, args.task_id, timeout)
        report_lint(problems, notes)
        return 3 if problems else 0
    turns = [int(m.group(1)) for p in td.glob("turn-*.md") if (m := re.fullmatch(r"turn-(\d+)\.md", p.name))]
    return print_next_steps(lane, args.task_id, max(turns, default=1), lint=True,
                            override=args.override, timeout=timeout)


def dispatch_interpreter() -> str:
    """An interpreter that can actually RUN dispatch.py, not just start it.

    `sys.executable` is the obvious choice and it is wrong often enough to
    matter: this script runs happily under 3.9, dispatch.py needs 3.11 for
    tomllib, and the failure lands at import time - before any marker is
    written - so a detached lane started from the printed command hangs a
    waiting architect forever. Printing a command we have not established can
    run is how that gets discovered three steps later.
    """
    for cand in (sys.executable, shutil.which("python3.13"), shutil.which("python3.12"),
                 shutil.which("python3.11"), shutil.which("python3"), shutil.which("python")):
        if not cand:
            continue
        out = subprocess.run([cand, "-c", "import tomllib; print('DISPATCH_OK')"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
        if out.stdout.strip() == "DISPATCH_OK":
            return cand
    raise SystemExit(
        "BLOCK: no interpreter here can run dispatch.py (it needs Python 3.11+ for "
        "tomllib). Install one - python3.13/3.12/3.11, or `brew install python`. "
        "If a lane was already opened, it is intact: re-run this script's `turn` "
        "subcommand afterwards to get the dispatch line, rather than opening again.")


def dispatch_extra(td: Path) -> list[str]:
    """--timeout/--model/--effort serit recorded in lane.json, re-validated.

    lane.json is writable by the worker, so nothing from it reaches the
    printed command line unless it is a plain number or word.
    """
    extra = read_record(td).get("dispatch")
    out: list[str] = []
    if not isinstance(extra, dict):
        return out
    timeout = extra.get("timeout")
    if isinstance(timeout, int) and not isinstance(timeout, bool) and 60 <= timeout <= 86400:
        out.append(f"--timeout {timeout}")
    for key in ("model", "effort"):
        value = extra.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
            out.append(f"--{key} {value}")
    return out


def print_next_steps(lane: Path, task_id: str, turn: int, lint: bool = True,
                     override: bool = False, timeout: float = LINT_TIMEOUT) -> int:
    """Print the dispatch line; 0 when printed, 3 when the spec or the lint withheld it."""
    td = task_dir(lane, task_id)
    state = spec_state(lane, task_id)
    if state != "ok":
        # The one judgment step the scaffolder will not fake. A lane dispatched
        # against an absent spec burns a full turn and returns a worker asking
        # what to do.
        print(f"SPEC.md is {state}. Write {td / 'SPEC.md'} "
              f"(template: {SKILL_ROOT / 'references' / 'spec-template.md'}), then re-run "
              f"this script's `turn` subcommand for the dispatch line.")
        return 3
    if lint:
        problems, notes = lint_lane(lane, task_id, timeout)
        report_lint(problems, notes)
        if problems and not override:
            print(f"DISPATCH LINE WITHHELD: {len(problems)} lint problem(s) above would stop the worker. "
                  f"Fix them, then run: \"{sys.executable}\" \"{Path(__file__).resolve()}\" lint "
                  f"--lane \"{lane}\" --task-id \"{task_id}\"  (add --override to print the line anyway)")
            return 3
        if problems:
            print("lint: --override given - printing the dispatch line despite the problems above")
    py = dispatch_interpreter()          # may BLOCK - resolve before announcing
    print("dispatch (detached; poll the DONE file, do not build a watcher loop):")
    argv = [f'"{py}"', f'"{DISPATCH}"',
            f'--task-dir "{td}"', f'--repo "{lane}"',
            f'--prompt-file "{td / "PROMPT.txt"}"', f'--done-file "{td / "DONE"}"',
            *dispatch_extra(td)]
    if os.name == "nt":
        # One line: `\` is not a continuation in PowerShell or cmd, and a
        # pasted multi-line command silently truncates at the first break.
        print("  " + " ".join(argv))
    else:
        print("  " + argv[0] + " " + argv[1] + " \\")
        print("      " + " ".join(argv[2:4]) + " \\")
        print("      " + " ".join(argv[4:]))
    print()
    print(f"close when done:  \"{py}\" \"{Path(__file__).resolve()}\" close "
          f"--lane \"{lane}\" --task-id \"{task_id}\"")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    lane = Path(args.lane).resolve()
    if not live_worktree(lane):
        # Deleted by hand, or half-removed by an earlier close: either way the
        # registration, the leftover files and the trust entry still need
        # clearing, and refusing here left that debris with no way out.
        return close_leftover(lane, args.repo, args.task_id, args.no_untrust)
    main = main_repo_of(lane)
    repo = Path(args.repo).resolve() if args.repo else main
    if repo != main:
        # Anything but this lane's own main worktree puts the archive where
        # nobody will look for it, and two variants do it silently: a SIBLING
        # lane (disposable - the evidence dies with it) and a SUBDIRECTORY of
        # the main repo, where close still returned 0. Measured, both.
        raise SystemExit(f"BLOCK: --repo {repo} is not this lane's main worktree ({main})")
    td = task_dir(lane, args.task_id)
    if not td.is_dir():
        # Without this, a wrong --task-id archives an empty directory and then
        # force-removes the whole lane: SPEC, changelogs, findings and probes
        # gone, rc=0. `turn` has always checked this; `close` is the one that
        # deletes, so it needed it more.
        raise SystemExit(f"BLOCK: no run directory at {td} - check --task-id "
                         f"(present: {[p.name for p in (lane / '.delegate-runs').iterdir()] if (lane / '.delegate-runs').is_dir() else 'none'})")

    # Unintegrated work is the one thing `--force` on a worktree can destroy
    # for good. The skill says an abandoned lane is removed only after the user
    # approves; this is that approval, made explicit.
    # A commit inside the lane is invisible to `status`, and removing the
    # worktree takes its reflog with it - the objects survive with nothing
    # pointing at them. Measured: a lane whose work was committed closed with
    # rc=0, a clean status, and an archive holding only the run files. Workers
    # may commit on the lane's detached HEAD, and those commits are the one
    # thing here that cannot be recovered from the archive - `integrate` moves them.
    recorded = None
    try:
        record = json.loads((td / "lane.json").read_text(encoding="utf-8"))
        recorded = record.get("base_sha") if isinstance(record, dict) else None
        if not isinstance(recorded, str) or not recorded:
            recorded = None
    except (OSError, ValueError):
        recorded = None
    record_now = read_record(td)
    if not args.force:
        if recorded is None:
            # An unreadable record must not downgrade a guard on a destructive
            # step. Every malformed shape - absent file, {}, empty string,
            # null, truncated JSON - used to land here and skip the check
            # silently, and the lane was force-removed with its commits in it.
            # The function that only rebuilds a prompt refuses to guess in
            # exactly this situation; the one that deletes cannot be laxer.
            raise SystemExit(
                f"BLOCK: cannot read a base commit from {td / 'lane.json'}, so whether "
                "this lane carries unintegrated commits is unknown. Check with "
                f"`git -C {lane} log --oneline`, then pass --force to remove it anyway.")
        head = git(["rev-parse", "HEAD"], cwd=lane, check=False)
        if head and head != recorded and head != record_now.get("integrated_head"):
            raise SystemExit(
                f"BLOCK: the lane is at {head[:12]}, not the base {recorded[:12]} it was "
                "opened from - it carries commits that exist nowhere else. Integrate "
                "them (git -C <main> cherry-pick, or apply the diff), or pass --force "
                "to discard them.")

    # Links first: they would show as untracked, and a removal that walks
    # through one would reach the real venv. Unlink only the link itself.
    link_rels = None
    try:
        record = json.loads((td / "lane.json").read_text(encoding="utf-8"))
        if isinstance(record, dict) and isinstance(record.get("links"), list):
            link_rels = [x for x in record["links"] if isinstance(x, str)]
    except (OSError, ValueError):
        link_rels = None
    if link_rels is None:
        link_rels = load_config(repo)["links"]
    unlink_links(lane, link_rels)
    verify_sources(repo, link_rels)
    left = remaining_links(lane, link_rels)
    if left:
        return link_block(lane, left)

    dirty = git(["status", "--porcelain", "-uall", "--", ".", ":(exclude).delegate-runs"],
                cwd=lane, check=False)
    if (dirty and not args.force and record_now.get("integrated_tree")
            and git(["rev-parse", "HEAD"], cwd=lane, check=False) == record_now.get("integrated_head")
            and lane_fingerprint(lane) == record_now["integrated_tree"]):
        dirty = ""
    if dirty and not args.force:
        print("BLOCK: the lane has changes outside .delegate-runs that are not archived:")
        print(dirty)
        print("Integrate them, or re-run with --force if they are genuinely disposable.")
        return 1

    archive = repo / ".delegate-runs" / "ARCHIVE" / args.task_id
    archive.mkdir(parents=True, exist_ok=True)
    kept = []
    copied = []                               # source files the archive must match
    if td.is_dir():
        for item in sorted(td.iterdir()):
            if item.name in ("RAW_OUTPUT.log", "PROMPT.txt", "finding-contract.md"):
                continue                      # transcript and inputs, not the record
            dest = archive / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
                copied.extend(p for p in sorted(item.rglob("*")) if p.is_file())
            else:
                shutil.copyfile(item, dest)
                copied.append(item)
            kept.append(item.name)
    raw = td / "RAW_OUTPUT.log"
    if raw.is_file():
        # The full transcript is too big to keep and dies with the worktree,
        # but two parts of it are verdict evidence elsewhere in the protocol:
        # the [decline]/[approve] lines (what exit 5 actually means for this
        # lane) and the tail (why a zero-findings lane returned nothing). Keep
        # a bounded slice rather than losing the answer to both questions.
        lines = raw.read_text(encoding="utf-8", errors="replace").splitlines()
        marked = [ln for ln in lines if ln.startswith(("[decline]", "[approve]"))]
        slice_ = marked + ["", f"--- last 200 of {len(lines)} lines ---"] + lines[-200:]
        (archive / "RAW_OUTPUT.tail.log").write_text("\n".join(slice_) + "\n",
                                                     encoding="utf-8")
        kept.append(f"RAW_OUTPUT.tail.log ({len(marked)} approval lines + tail)")
    # Verify the archive BEFORE the irreversible step. A copy that silently
    # did not happen looks exactly like one that did, and the next line
    # destroys the original: a field round removed nine lanes this way and
    # lost every proof script and finding text they held. Existence is not
    # enough either - a truncated or empty destination passes that check -
    # so compare content.
    mismatch = []
    for src in copied:
        dest = archive / src.relative_to(td)
        try:
            if not (dest.is_file() and _digest(dest) == _digest(src)):
                mismatch.append(str(src.relative_to(td)))
        except OSError as exc:
            mismatch.append(f"{src.relative_to(td)} ({exc.strerror})")
    if mismatch:
        print("BLOCK: the archive does not match the lane - worktree NOT removed.")
        for name in mismatch[:20]:
            print(f"  ! {name}")
        if len(mismatch) > 20:
            print(f"  ... and {len(mismatch) - 20} more")
        print(f"Archive: {archive}")
        print("Free space, permissions and path length are the usual causes.")
        print("Fix the archive, then run close again - the lane is still intact.")
        return 1
    manifest = [f"{_digest(archive / p.relative_to(td))}  {p.relative_to(td)}"
                for p in copied]
    (archive / "MANIFEST.sha256").write_text(
        "\n".join(manifest) + "\n", encoding="utf-8")

    print(f"archived -> {archive}")
    for name in kept:
        print(f"  + {name}")
    print(f"  verified {len(copied)} file(s) by sha256 -> MANIFEST.sha256")

    if remove_worktree(repo, lane):
        return 1
    drop_accept_scratch(archive, args.task_id)
    if not args.no_untrust:
        untrust(lane)
    return 0


def remove_worktree(repo: Path, path: Path) -> int:
    """`git worktree remove --force`, finishing by hand what it leaves behind."""
    out = subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=str(repo),
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode == 0:
        git(["worktree", "prune"], cwd=repo)
        print(f"removed worktree {path}")
        return 0
    print(f"WARNING: git worktree remove failed: {out.stderr.strip()[:500]}")
    return clear_leftover(repo, path)


def eol_style(data: bytes) -> bytes | None:
    """b"\\r\\n" or b"\\n" when a text file ends every line the same way, else None."""
    lf = data.count(b"\n")
    if b"\0" in data or not lf:
        return None
    crlf = data.count(b"\r\n")
    return b"\r\n" if crlf == lf else b"\n" if not crlf else None


def eol_attributes(repo: Path, rels: list[str]) -> tuple[dict[str, bytes], set[str]]:
    """({path: ending .gitattributes pins}, {paths marked -text})."""
    if not rels:
        return {}, set()
    out = subprocess.run(["git", "check-attr", "-z", "--stdin", "eol", "text"], cwd=str(repo),
                         input=b"\0".join(r.encode("utf-8") for r in rels), capture_output=True).stdout
    fields = out.split(b"\0")
    pinned, binary = {}, set()
    for path, attr, value in zip(fields[0::3], fields[1::3], fields[2::3]):
        rel = path.decode("utf-8", errors="replace")
        if attr == b"eol" and value in (b"lf", b"crlf"):
            pinned[rel] = b"\r\n" if value == b"crlf" else b"\n"
        elif attr == b"text" and value == b"unset":
            binary.add(rel)
    return pinned, binary


def eol_snapshot(main: Path, rels: list[str]) -> dict[str, bytes | None]:
    """Each path's line ending in the main tree before integrating; None = new file."""
    before = {}
    for rel in rels:
        path = main / rel
        before[rel] = (eol_style(path.read_bytes()) or b"?") if path.is_file() else None
    return before


def restore_eol(main: Path, before: dict[str, bytes | None]) -> list[str]:
    """Put each integrated file back on the line ending it had, or that is pinned for it.

    Measured: integrated files arrived CRLF in a tree that holds them LF. Git
    for Windows ships core.autocrlf=true, so `git apply` and `cherry-pick`
    write CRLF for any path without an eol attribute, whatever ending the
    main tree's copy had; and a new file is copied byte for byte, so a worker
    that wrote CRLF delivers CRLF even where .gitattributes says eol=lf.
    """
    rels = list(before)
    pinned, binary = eol_attributes(main, rels)
    changed = []
    for rel in rels:
        path = main / rel
        want = before[rel] if before[rel] is not None else pinned.get(rel)
        if rel in binary or want not in (b"\n", b"\r\n") or path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        fixed = data.replace(b"\r\n", b"\n")
        if want == b"\r\n":
            fixed = fixed.replace(b"\n", b"\r\n")
        if fixed != data:
            path.write_bytes(fixed)
            changed.append(rel)
    return changed


def cmd_integrate(args: argparse.Namespace) -> int:
    lane = Path(args.lane).resolve()
    if not (lane / ".git").exists():
        raise SystemExit(f"BLOCK: {lane} is not a git worktree - check --lane")
    main = main_repo_of(lane)
    if not is_linked_worktree(lane):
        raise SystemExit(f"BLOCK: {lane} is the main tree, not a lane")
    td = task_dir(lane, args.task_id)
    record = read_record(td)
    base = record.get("base_sha")
    if not isinstance(base, str) or not base:
        raise SystemExit(f"BLOCK: cannot read a base commit from {td / 'lane.json'} - "
                         "integrating without it could copy the wrong range")
    link_rels = [x for x in record.get("links", []) if isinstance(x, str)]
    head = git(["rev-parse", "HEAD"], cwd=lane)

    dirty = git(["status", "--porcelain", "-uall", "--", ".", ":(exclude).delegate-runs"],
                cwd=main, check=False)
    if dirty:
        print(f"BLOCK: the main tree {main} has uncommitted changes; integrating on top of them "
              "would mix them with the lane's:")
        print(dirty)
        print("Commit or stash them, then run integrate again.")
        return 1

    exclude = [".", ":(exclude).delegate-runs"]
    changed_names = subprocess.run(["git", "diff", "--name-only", "-z", base, "--", *exclude],
                                   cwd=str(lane), capture_output=True).stdout
    before = eol_snapshot(main, [x.decode("utf-8", errors="replace")
                                 for x in changed_names.split(b"\0") if x])

    commits = git(["rev-list", "--reverse", f"{base}..{head}"], cwd=lane).split()
    if commits and record.get("integrated_head") != head:
        out = subprocess.run(["git", "cherry-pick", *commits], cwd=str(main),
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        if out.returncode != 0:
            subprocess.run(["git", "cherry-pick", "--abort"], cwd=str(main), capture_output=True)
            print(f"BLOCK: cherry-picking {len(commits)} lane commit(s) onto the main tree failed "
                  "and was aborted; the main tree is unchanged:")
            print((out.stdout + out.stderr).strip())
            return 1
        print(f"cherry-picked {len(commits)} commit(s) onto the main tree")

    patch =subprocess.run(["git", "diff", "HEAD", "--binary", "--", *exclude], cwd=str(lane),
                           capture_output=True).stdout
    others = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z",
                             "--", *exclude], cwd=str(lane), capture_output=True).stdout
    untracked = []
    for raw in sorted(x for x in others.split(b"\0") if x):
        rel = raw.decode("utf-8", errors="replace")
        if any(rel == link or rel.startswith(link.rstrip("/") + "/") for link in link_rels):
            continue
        untracked.append(rel)
    clash = [rel for rel in untracked if os.path.lexists(main / rel)]
    if clash:
        print("BLOCK: these lane files are untracked there but already exist in the main tree; "
              "nothing of the uncommitted work was copied:")
        for rel in clash:
            print(f"  ! {rel}")
        return 1
    if patch:
        check = subprocess.run(["git", "apply", "--binary", "--check"], cwd=str(main),
                               input=patch, capture_output=True)
        if check.returncode != 0:
            print("BLOCK: the lane's uncommitted diff does not apply to the main tree; "
                  "nothing of the uncommitted work was copied:")
            print(check.stderr.decode("utf-8", errors="replace").strip())
            return 1
        applied = subprocess.run(["git", "apply", "--binary"], cwd=str(main),
                                 input=patch, capture_output=True)
        if applied.returncode != 0:
            raise SystemExit("BLOCK: git apply failed after a clean --check: "
                             + applied.stderr.decode("utf-8", errors="replace").strip())
    for rel in untracked:
        target = main / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(lane / rel, target, follow_symlinks=False)
    before.update({rel: None for rel in untracked})
    restored = restore_eol(main, before)
    if restored:
        print(f"line endings restored to the main tree's own in {len(restored)} file(s): "
              + ", ".join(restored[:10]) + (" ..." if len(restored) > 10 else ""))

    record["integrated_head"] = head
    record["integrated_tree"] = lane_fingerprint(lane)
    (td / "lane.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"integrated {lane} into {main}: {len(commits)} commit(s), "
          f"{'a tracked diff, ' if patch else ''}{len(untracked)} untracked file(s)")
    print("The uncommitted part is in the main tree as plain working-tree changes - review, then commit.")
    return 0


def cmd_blind_open(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    if is_linked_worktree(repo):
        raise SystemExit(f"BLOCK: {repo} is itself a lane - open blind worktrees from the main tree")
    base = git(["rev-parse", args.base], cwd=repo)
    wt = Path(args.dir).resolve()
    if wt.exists():
        raise SystemExit(f"BLOCK: {wt} already exists - remove it first, or pick another --dir")
    for f in [args.brief, *args.input]:
        if f is not None and not Path(f).is_file():
            raise SystemExit(f"BLOCK: {f} is not a file")
    cfg = load_config(repo)
    links: list[str] = []
    git(["worktree", "add", "--detach", str(wt), base], cwd=repo)
    try:
        head = git(["rev-parse", "HEAD"], cwd=wt)
        if head != base:
            raise SystemExit(f"BLOCK: blind worktree HEAD {head} != requested base {base}")
        links = link_into(repo, wt, cfg["links"])
        verify = wt / "verify"
        verify.mkdir()
        if args.brief:
            shutil.copyfile(args.brief, verify / "SPEC.md")
        for f in args.input:
            shutil.copyfile(f, verify / Path(f).name)
        if args.probes_from:
            copy_probes(args.probes_from, verify / "prior-probes", "blind")
        blind_record = {"base_sha": base, "links": links}
        # Its own range, not its lane's: the blind auditor runs the same probes
        # at the same time as the Codex lane.
        ports = allocate_ports(repo, cfg)
        if ports:
            blind_record.update(port_base=ports[0], port_count=ports[1])
        (verify / ".blind.json").write_text(json.dumps(blind_record, indent=2), encoding="utf-8")
    except BaseException:
        unlink_links(wt, links)
        git(["worktree", "remove", "--force", str(wt)], cwd=repo, check=False)
        git(["worktree", "prune"], cwd=repo, check=False)
        if wt.exists() or registered_worktree(repo, wt):
            print(f"ROLLBACK INCOMPLETE: {wt} is still there - remove it by hand "
                  f"(git -C {repo} worktree remove --force {wt})", file=sys.stderr)
        else:
            print(f"rolled back: removed the half-built blind worktree at {wt}", file=sys.stderr)
        raise
    print(wt)
    return 0


def cmd_blind_close(args: argparse.Namespace) -> int:
    wt = Path(args.dir).resolve()
    if not live_worktree(wt):
        return close_leftover(wt, args.repo, None, True)
    record_path = wt / "verify" / ".blind.json"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        rels = [x for x in record.get("links", []) if isinstance(x, str)]
    except (OSError, ValueError, AttributeError):
        raise SystemExit(f"BLOCK: cannot read {record_path} - this is not a blind worktree "
                         "made by blind-open, so its links are unknown. Remove it by hand.")
    main = main_repo_of(wt)
    unlink_links(wt, rels)
    verify_sources(main, rels)
    left = remaining_links(wt, rels)
    if left:
        return link_block(wt, left)
    if remove_worktree(main, wt):
        return 1
    verify_sources(main, rels)
    return 0


def main() -> int:
    # Windows reports OS errors in the user's language; measured, a Turkish
    # "Permission denied" crashed close mid-report on a cp1252 pipe.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    op = sub.add_parser("open", help="create and scaffold a lane")
    op.add_argument("--task-id", required=True)
    op.add_argument("--repo", type=Path, default=Path.cwd())
    op.add_argument("--base", default="HEAD", help="commit-ish the lane is pinned to")
    op.add_argument("--lane", type=Path, default=None,
                    help="worktree path (default: ../<repo>-lanes/<task-id>)")
    op.add_argument("--mode", choices=list(MODES), default="worker")
    op.add_argument("--spec", type=Path, default=None, help="SPEC.md to install in the lane")
    op.add_argument("--tool", action="append", default=[], metavar="NAME=PATH",
                    help="wrap an out-of-lane executable (a project venv interpreter, "
                         "a toolchain binary) in a lane-local NAME.cmd/NAME.sh the "
                         "acceptance command can call. Repeatable. Without this, an "
                         "acceptance command naming the path directly is declined by "
                         "the sandbox and the turn produces nothing.")
    op.add_argument("--probes-from", type=Path, default=None, metavar="DIR",
                    help="copy every file in DIR into the new probes/, rewriting the old "
                         "task id (from DIR/../lane.json) to this one")
    op.add_argument("--accept", action="store_true",
                    help="write <task dir>/ACCEPT.py: changelog check, probes, and the "
                         f"[accept] commands from {CONFIG_NAME}")
    op.add_argument("--turn", type=int, default=1)
    op.add_argument("--no-trust", action="store_true",
                    help="skip doctor.py --trust (the worker will not run without it)")
    lint_args = [
        (("--no-lint",), {"action": "store_true",
                          "help": "skip the pre-dispatch probe lint (serit runs `lint` itself later)"}),
        (("--lint-override",), {"action": "store_true",
                                "help": "print the dispatch line even when the lint finds problems"}),
        (("--lint-timeout",), {"type": float, "default": None, "metavar": "SECONDS",
                               "help": f"per-probe lint timeout (default: [lint] timeout in "
                                       f"{CONFIG_NAME}, else {LINT_TIMEOUT}s); slower probes are skipped"}),
    ]
    for names, kw in lint_args:
        op.add_argument(*names, **kw)
    op.set_defaults(func=cmd_open)

    tp = sub.add_parser("turn", help="seed the next turn and re-point PROMPT.txt")
    tp.add_argument("--lane", type=Path, required=True)
    tp.add_argument("--task-id", required=True)
    tp.add_argument("--turn", type=int, required=True)
    tp.add_argument("--mode", choices=list(MODES), default=None,
                    help="override the mode recorded by `open` (normally unnecessary)")
    tp.add_argument("--recovery", action="store_true",
                    help="use the recovery instruction line (assess the tree, finish what is missing)")
    for names, kw in lint_args:
        tp.add_argument(*names, **kw)
    tp.set_defaults(func=cmd_turn)

    lp = sub.add_parser("lint", help="re-run the pre-dispatch probe lint; print the dispatch line when clean")
    lp.add_argument("--lane", type=Path, required=True)
    lp.add_argument("--task-id", required=True)
    lp.add_argument("--override", action="store_true",
                    help="print the dispatch line even when the lint finds problems")
    lp.add_argument("--report-only", action="store_true",
                    help="never print the dispatch line; exit 3 on problems, 0 when clean")
    lp.add_argument("--lint-timeout", type=float, default=None, metavar="SECONDS")
    lp.set_defaults(func=cmd_lint)

    cp = sub.add_parser("close", help="archive the contract, remove the worktree, drop trust")
    cp.add_argument("--lane", type=Path, required=True)
    cp.add_argument("--task-id", required=True)
    cp.add_argument("--repo", type=Path, default=None,
                    help="main repo (default: resolved from the lane itself, which is "
                         "correct even when you are standing inside the lane)")
    cp.add_argument("--force", action="store_true",
                    help="remove the lane even though it carries unintegrated changes")
    cp.add_argument("--no-untrust", action="store_true",
                    help="leave the worker config alone (for lanes opened with --no-trust)")
    cp.set_defaults(func=cmd_close)

    ip = sub.add_parser("integrate", help="move a lane's commits and uncommitted changes into the main tree")
    ip.add_argument("--lane", type=Path, required=True)
    ip.add_argument("--task-id", required=True)
    ip.set_defaults(func=cmd_integrate)

    bo = sub.add_parser("blind-open", help="detached blind-audit worktree with links and verify/")
    bo.add_argument("--repo", type=Path, default=Path.cwd())
    bo.add_argument("--base", required=True, help="commit-ish the worktree is pinned to")
    bo.add_argument("--dir", type=Path, required=True, help="worktree path")
    bo.add_argument("--brief", type=Path, default=None, help="copied to verify/SPEC.md")
    bo.add_argument("--input", type=Path, action="append", default=[], metavar="FILE",
                    help="copied into verify/ under its own name. Repeatable.")
    bo.add_argument("--probes-from", type=Path, default=None, metavar="DIR",
                    help="copy DIR's files to verify/prior-probes/ (task id rewritten to 'blind')")
    bo.set_defaults(func=cmd_blind_open)

    bc = sub.add_parser("blind-close", help="remove a blind worktree and its links")
    bc.add_argument("--dir", type=Path, required=True)
    bc.add_argument("--repo", type=Path, default=None,
                    help="main repo, only needed when --dir is debris git no longer lists "
                         "and is not under <repo>-lanes")
    bc.set_defaults(func=cmd_blind_close)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
