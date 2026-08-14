#!/usr/bin/env python3
"""Run every audit probe once and print one verdict table.

Why a script rather than a loop the architect types each round: the loop is
where the measurements were lost. Measured - the operator piped a
probe into `| tail`, the pipeline reported the exit code of `tail`, and three
probes read as passing while nothing had been measured. That is the same class
as the stale-root bug this runner also checks for: the verification step
itself measuring the wrong thing. subprocess.run() reads the child's status
directly, so there is no pipeline to swallow it.

Why `.py` and not the `.sh` the field note asked for: `codex-audit` §4
documents a Windows machine whose only `bash` on PATH is the WSL launcher stub
that cannot run any script. A shell runner cannot be the thing that verifies
the shell. Python is already a hard requirement of this plugin (dispatch.py
needs 3.11+), so it is the one interpreter both platforms are known to have.

The two signals `codex-audit` §4 makes binding are enforced here, not trusted:

  1. per RUN - the runner must answer its handshake (`PROBE_SHELL_OK` for
     bash and PowerShell, `PY_OK` for python) before any rc from it counts as
     a verdict;
  2. per PROBE - the probe must print `probe root: <root>` (its own first
     line). No marker means the probe never executed and its rc belongs to
     whatever failed instead, whatever the number was.

Exit codes (the runner's own, not a probe's):
  0  every probe produced a verdict, and every verdict measured the expected root
  1  at least one probe produced NO verdict - no runner, did not run, timed out,
     wrong root, an rc outside the contract, or a probe file nothing here can
     run. The table cannot be trusted as a whole, which is a finding about the
     audit, not about the code.
  2  every probe produced a verdict and one or more said `2` (probe invalid),
     which `codex-audit` §4 treats as a finding in its own right.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

MARKER = "probe root:"

# Handshake strings are verbatim from codex-audit SKILL.md §1 and §4, and are
# checked by OUTPUT, never by exit code: the WSL stub exits 1, which is also
# the contract's code for "the flaw reproduces", so an rc-based check reads a
# broken launcher as a confirmed finding.
RUNNERS: dict[str, dict] = {
    ".sh": {
        "expect": "PROBE_SHELL_OK",
        "handshake": lambda exe: [exe, "-c", "echo PROBE_SHELL_OK"],
        "invoke": lambda exe, probe: [exe, str(probe)],
    },
    ".py": {
        "expect": "PY_OK",
        "handshake": lambda exe: [exe, "-c", 'print("PY_OK")'],
        "invoke": lambda exe, probe: [exe, str(probe)],
    },
    # PowerShell is a first-class probe language for the same reason the skill
    # allows .py: on a Windows machine with no working bash it may be the only
    # thing that can exercise the behaviour at all.
    ".ps1": {
        "expect": "PROBE_SHELL_OK",
        "handshake": lambda exe: [exe, "-NoProfile", "-Command", "Write-Output PROBE_SHELL_OK"],
        "invoke": lambda exe, probe: [exe, "-NoProfile", "-File", str(probe)],
    },
}

# Everything that is not a runnable probe and not obviously a note fails the
# run loudly. The allowlist runs this way round on purpose: an earlier version
# listed the script extensions to complain about, and `check.bash`, `PROBE.SH`
# and an extensionless `authz-1` all vanished from the table without changing
# the exit code. Unknown means unmeasured, and unmeasured has to be visible.
DOC_NOISE = {".md", ".txt", ".json", ".log", ".csv", ".yml", ".yaml", ".toml",
             ".ini", ".rst", ".out", ".diff", ".patch"}


def runner_candidates(suffix: str, override: str | None) -> list[str]:
    """Interpreters to try for this probe type, best-known-good first."""
    if override:
        return [override]
    if suffix == ".py":
        # The interpreter running this file has already proved it works. The
        # named fallbacks matter on Windows, where `python3` is often the
        # WindowsApps stub that prints "Python was not found" and exits 9009.
        found = [sys.executable, shutil.which("python3"),
                 shutil.which("python"), shutil.which("py")]
    elif suffix == ".ps1":
        found = [shutil.which("pwsh"), shutil.which("powershell")]
    else:
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        git_bash = [rf"{pf}\Git\bin\bash.exe", rf"{pf}\Git\usr\bin\bash.exe"]
        # Git Bash BEFORE the PATH entry on Windows. The handshake already
        # rejects the WSL launcher stub, but a machine with WSL genuinely
        # installed passes the handshake and then runs every probe inside the
        # WSL filesystem, where the Windows path it was handed does not exist:
        # all probes come back DID-NOT-RUN with a working shell. Git Bash
        # understands the paths the rest of this plugin produces.
        found = (git_bash + [shutil.which("bash")]) if os.name == "nt" \
            else [shutil.which("bash")]
    seen: list[str] = []
    for c in found:
        if c and c not in seen:
            seen.append(c)
    return seen


def resolve_runner(suffix: str, override: str | None) -> tuple[str | None, str]:
    """(interpreter, note) for this probe type - None when none answers."""
    spec = RUNNERS[suffix]
    tried: list[str] = []
    for cand in runner_candidates(suffix, override):
        tried.append(cand)
        try:
            out = subprocess.run(spec["handshake"](cand), capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.stdout.strip() == spec["expect"]:
            return cand, f"{suffix} runner: {cand}"
    return None, f"{suffix} runner: NONE ANSWERED (tried: {tried or 'nothing found'})"


def same_tree(a: str, b: str) -> bool:
    """Do two path strings name the same tree?

    Lexical first, then realpath: /tmp is a symlink to /private/tmp on macOS,
    and a probe that echoes one while the architect passed the other is
    correct, not stale. Comparing raw strings would raise a false alarm on
    every macOS run in a temp dir.
    """
    na, nb = (os.path.normcase(os.path.normpath(p)) for p in (a, b))
    if na == nb:
        return True
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return False


def collect(targets: list[Path]) -> tuple[list[Path], list[Path], list[Path]]:
    """(probes, unrunnable, missing) - everything named, nothing dropped quietly."""
    probes: list[Path] = []
    unrunnable: list[Path] = []
    missing: list[Path] = []
    for t in targets:
        t = t.resolve()          # probes run with cwd=root; a relative target
        if t.is_dir():           # would otherwise resolve against the wrong dir
            # rglob, not iterdir: a probe filed under probes/<lens>/ is still a
            # probe, and skipping it silently is the failure mode above.
            for p in sorted(t.rglob("*")):
                if not p.is_file():
                    continue
                # Hidden-file check runs on the path BELOW the target only.
                # Written against p.parts it walked the ancestors too, and the
                # documented probes directory lives under `.delegate-runs/` -
                # so every probe in every real run was filtered out and the
                # runner announced "nothing to measure" with rc=0. Measured on
                # the exact command in codex-audit SKILL.md.
                if any(part.startswith(".") for part in p.relative_to(t).parts):
                    continue
                if p.suffix.lower() in RUNNERS:
                    probes.append(p)
                elif p.suffix.lower() not in DOC_NOISE:
                    unrunnable.append(p)
        elif t.is_file():
            (probes if t.suffix.lower() in RUNNERS else unrunnable).append(t)
        else:
            missing.append(t)
    return probes, unrunnable, missing


def label_for(probes: list[Path]) -> dict[Path, str]:
    """Display names, disambiguated only where two probes share a filename."""
    counts: dict[str, int] = {}
    for p in probes:
        counts[p.name] = counts.get(p.name, 0) + 1
    return {p: (p.name if counts[p.name] == 1 else str(Path(p.parent.name) / p.name))
            for p in probes}


def run_probe(probe: Path, runner: str | None, suffix: str, root: Path, timeout: int) -> dict:
    """One probe, one row. Never raises - a crashed probe is data, not a stop."""
    row: dict = {"probe": probe.name, "path": str(probe), "rc": None,
                 "verdict": "", "root": "", "root_ok": False, "note": ""}
    if runner is None:
        row["verdict"], row["note"] = "NO-RUNNER", "no interpreter answered its handshake"
        return row
    env = {**os.environ, "AUDIT_ROOT": str(root)}
    try:
        out = subprocess.run(RUNNERS[suffix]["invoke"](runner, probe), cwd=str(root), env=env,
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        row["verdict"], row["note"] = "TIMEOUT", f"no result within {timeout}s"
        return row
    except OSError as exc:
        row["verdict"], row["note"] = "DID-NOT-RUN", f"cannot execute: {exc}"
        return row
    row["rc"] = out.returncode
    # The contract puts the marker on stderr, but stdout is read too: the point
    # here is to establish that the probe body executed, and refusing to see a
    # marker in the wrong stream would turn a formatting slip into a phantom
    # "did not run".
    for stream in (out.stderr, out.stdout):
        for line in stream.splitlines():
            if line.strip().startswith(MARKER):
                row["root"] = line.split(MARKER, 1)[1].strip()
                break
        if row["root"]:
            break
    if not row["root"]:
        row["verdict"] = "DID-NOT-RUN"
        row["note"] = f"no '{MARKER}' line - rc={out.returncode} belongs to whatever failed instead"
        return row
    row["root_ok"] = same_tree(row["root"], str(root))
    if not row["root_ok"]:
        row["verdict"] = "WRONG-TREE"
        row["note"] = f"measured {row['root']}, expected {root}"
        return row
    row["verdict"] = {1: "LIVE", 0: "FIXED", 2: "INVALID"}.get(out.returncode, "BAD-RC")
    if row["verdict"] == "BAD-RC":
        row["note"] = f"rc={out.returncode} is outside the 0/1/2 contract"
    elif row["verdict"] == "INVALID":
        row["note"] = (out.stderr.strip().splitlines() or [""])[-1][:120]
    return row


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run audit probes and print a verdict table you can paste into the report.")
    ap.add_argument("targets", nargs="+", type=Path,
                    help="probe directories and/or individual probe files")
    ap.add_argument("--root", type=Path, default=None,
                    help="tree the probes must measure (default: the git root of the cwd). "
                         "This is what AUDIT_ROOT is set to - point it at the FIXED tree, "
                         "never at the lane the probes were written in.")
    ap.add_argument("--timeout", type=int, default=120, help="seconds per probe (default 120)")
    ap.add_argument("--json", type=Path, default=None, help="also write the rows here")
    ap.add_argument("--bash", default=None, metavar="PATH",
                    help="interpreter for .sh probes, when the resolved one is wrong")
    args = ap.parse_args()

    root = args.root
    if root is None:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True)
        if out.returncode != 0 or not out.stdout.strip():
            print("ERROR: no --root given and the cwd is not inside a git tree.", file=sys.stderr)
            return 1
        root = Path(out.stdout.strip())
    if not root.is_dir():
        print(f"ERROR: --root {root} is not a directory - nothing there to measure.",
              file=sys.stderr)
        return 1
    root = root.resolve()

    probes, unrunnable, missing = collect(args.targets)
    if missing:
        print(f"ERROR: no such probe or directory: {[str(m) for m in missing]}", file=sys.stderr)
        return 1
    if not probes and not unrunnable:
        # A lens whose findings are all hygiene has no probes, and calling that
        # a failed run trains people to ignore the exit code.
        print(f"no probes under {[str(t) for t in args.targets]} - nothing to measure")
        return 0

    runners: dict[str, str | None] = {}
    for suffix in sorted({p.suffix.lower() for p in probes}):
        runner, note = resolve_runner(suffix, args.bash if suffix == ".sh" else None)
        runners[suffix] = runner
        print(note)
    print(f"measured against: {root}")
    if any(_is_within(p, root) for p in probes):
        # The 13/13 stale-tree failure in a second disguise: WRONG-TREE cannot
        # catch a --root that is itself the lane, because then the probe's
        # echoed root matches it exactly.
        print("WARNING: some probes live inside --root. If that root is the lane, every "
              "verdict measures the unfixed copy and every row will still say 'ok'.")
    print()

    rows = [run_probe(p, runners.get(p.suffix.lower()), p.suffix.lower(), root, args.timeout)
            for p in probes]
    labels = label_for(probes)
    for probe, row in zip(probes, rows):
        row["probe"] = labels[probe]
    for path in unrunnable:
        rows.append({"probe": path.name, "path": str(path), "rc": None,
                     "verdict": "NO-RUNNER", "root": "", "root_ok": False,
                     "note": f"no runner for '{path.suffix}' probes - it was never executed"})

    width = max(len(r["probe"]) for r in rows)
    print(f"{'probe'.ljust(width)}  rc   verdict      root")
    print(f"{'-' * width}  ---  -----------  ----")
    for r in rows:
        rc = "-" if r["rc"] is None else str(r["rc"])
        mark = "ok" if r["root_ok"] else (r["root"] or "-")
        print(f"{r['probe'].ljust(width)}  {rc.rjust(3)}  {r['verdict'].ljust(11)}  {mark}")
        if r["note"]:
            print(f"{' ' * width}       ^ {r['note']}")

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print()
    print("  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"root": str(root), "rows": rows}, indent=2),
                             encoding="utf-8")
        print(f"rows -> {args.json}")

    no_verdict = [r for r in rows if r["verdict"] not in ("LIVE", "FIXED", "INVALID")]
    if no_verdict:
        # Say it in words as well as in the exit code. A run where probes did
        # not execute is not a clean run, and the whole reason this file exists
        # is that the difference was invisible at a glance.
        print(f"\n{len(no_verdict)} probe(s) produced NO verdict - the table is incomplete, "
              "and no finding may be closed on it.")
        return 1
    if counts.get("INVALID"):
        print(f"\n{counts['INVALID']} probe(s) reported themselves invalid (rc=2). "
              "Per codex-audit §4 that is a finding: rewrite the probe against the new "
              "shape and re-verify, or log the finding as reopened.")
        return 2
    return 0


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    sys.exit(main())
