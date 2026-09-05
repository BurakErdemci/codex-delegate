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
import subprocess
import sys
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


def prune_only(repo: Path, lane: Path, no_untrust: bool) -> int:
    """Clear the registration and trust entry of a lane whose directory is gone."""
    git(["worktree", "prune"], cwd=repo, check=False)
    print(f"pruned the registration for {lane} (its directory was already gone - "
          "nothing could be archived, and any commit it held is already unreferenced)")
    if not no_untrust:
        subprocess.run([sys.executable, str(DOCTOR), "--untrust", str(lane)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
        print("dropped its trust entry")
    return 0


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
        resolved = Path(target).expanduser()
        if not resolved.exists():
            # A bridge to a missing executable fails inside the worker's turn,
            # where the error reads as the worker's fault. Fail here instead.
            raise SystemExit(f"BLOCK: --tool {name} points at {resolved}, which does not exist")
        resolved = resolved.resolve()

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
        if args.tool:
            created += write_tool_bridges(td, args.tool)

        # Written before the seeding so `turn` can find it even if the run dies
        # halfway: it is what stops a later turn from rebuilding an audit
        # PROMPT.txt as a worker one.
        (td / "lane.json").write_text(
            json.dumps({"task_id": args.task_id, "mode": args.mode, "base_sha": base},
                       indent=2), encoding="utf-8")
        created.append(str(td / "lane.json"))

        created += seed_turn(lane, args.task_id, args.turn, args.mode, recovery=False)

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
    print()
    print_next_steps(lane, args.task_id, args.turn)
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
    print()
    print_next_steps(lane, args.task_id, args.turn)
    return 0


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


def print_next_steps(lane: Path, task_id: str, turn: int) -> None:
    td = task_dir(lane, task_id)
    state = spec_state(lane, task_id)
    if state != "ok":
        # The one judgment step the scaffolder will not fake. A lane dispatched
        # against an absent spec burns a full turn and returns a worker asking
        # what to do.
        print(f"SPEC.md is {state}. Write {td / 'SPEC.md'} "
              f"(template: {SKILL_ROOT / 'references' / 'spec-template.md'}), then re-run "
              f"this script's `turn` subcommand for the dispatch line.")
        return
    py = dispatch_interpreter()          # may BLOCK - resolve before announcing
    print("dispatch (detached; poll the DONE file, do not build a watcher loop):")
    argv = [f'"{py}"', f'"{DISPATCH}"',
            f'--task-dir "{td}"', f'--repo "{lane}"',
            f'--prompt-file "{td / "PROMPT.txt"}"', f'--done-file "{td / "DONE"}"']
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


def cmd_close(args: argparse.Namespace) -> int:
    lane = Path(args.lane).resolve()
    if not (lane / ".git").exists():
        if args.repo and registered_worktree(Path(args.repo).resolve(), lane):
            # The directory was deleted by hand but git still lists it, and the
            # trust entry still exists. Refusing here left exactly the debris
            # this script was written to prevent, with no way to clear it.
            return prune_only(Path(args.repo).resolve(), lane, args.no_untrust)
        raise SystemExit(
            f"BLOCK: {lane} is not a git worktree - check --lane "
            "(if the directory was deleted by hand, pass --repo so the "
            "registration and trust entry can still be cleaned up)")
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
    # never run git, so this is the architect's own commit and it is the one
    # thing here that cannot be recovered from the archive.
    recorded = None
    try:
        record = json.loads((td / "lane.json").read_text(encoding="utf-8"))
        recorded = record.get("base_sha") if isinstance(record, dict) else None
        if not isinstance(recorded, str) or not recorded:
            recorded = None
    except (OSError, ValueError):
        recorded = None
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
        if head and head != recorded:
            raise SystemExit(
                f"BLOCK: the lane is at {head[:12]}, not the base {recorded[:12]} it was "
                "opened from - it carries commits that exist nowhere else. Integrate "
                "them (git -C <main> cherry-pick, or apply the diff), or pass --force "
                "to discard them.")

    dirty = git(["status", "--porcelain", "-uall", "--", ".", ":(exclude).delegate-runs"],
                cwd=lane, check=False)
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

    git(["worktree", "remove", "--force", str(lane)], cwd=repo)
    git(["worktree", "prune"], cwd=repo)
    print(f"removed worktree {lane}")
    if args.no_untrust:
        return 0
    out = subprocess.run([sys.executable, str(DOCTOR), "--untrust", str(lane)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(out.stdout.strip() or out.stderr.strip())
    if out.returncode != 0:
        # Not fatal - the worktree is already gone - but say it, because trust
        # entries accumulate invisibly and nothing else will mention it again.
        print(f"WARNING: trust entry for {lane} was not removed (rc={out.returncode})")
    return 0


def main() -> int:
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
    op.add_argument("--turn", type=int, default=1)
    op.add_argument("--no-trust", action="store_true",
                    help="skip doctor.py --trust (the worker will not run without it)")
    op.set_defaults(func=cmd_open)

    tp = sub.add_parser("turn", help="seed the next turn and re-point PROMPT.txt")
    tp.add_argument("--lane", type=Path, required=True)
    tp.add_argument("--task-id", required=True)
    tp.add_argument("--turn", type=int, required=True)
    tp.add_argument("--mode", choices=list(MODES), default=None,
                    help="override the mode recorded by `open` (normally unnecessary)")
    tp.add_argument("--recovery", action="store_true",
                    help="use the recovery instruction line (assess the tree, finish what is missing)")
    tp.set_defaults(func=cmd_turn)

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

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
