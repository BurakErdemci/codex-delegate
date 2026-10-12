#!/usr/bin/env python3
"""Checks for new-lane.py's lane config extensions and pre-dispatch lint.

Run: python test_lane_features.py    (exit 0 = every case holds)

Each case pins one field failure from 12 Oct 2026: ACCEPT.py dying on Turkish
probe output under a cp1252 pipe, two lanes' probes sharing a port, a probe
needing a file deleted that Codex cannot delete, and a probe that crashed
instead of failing. Temp git repos only; no network, no lanes of a real repo.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("new_lane_features", HERE / "new-lane.py")
nl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nl)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f" :: {detail[-1500:]}" if detail and not ok else ""))


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return path


def commit_all(repo: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base"], cwd=repo,
                   check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


TURKISH_PROBE = 'print("probe says çğışöü İ")\n'


def accept_unicode(tmp: Path) -> None:
    """A1: a probe printing Turkish passes ACCEPT.py on a cp1252 pipe."""
    lane = git_repo(tmp / "lane")
    td = lane / ".delegate-runs" / "u1"
    (td / "probes").mkdir(parents=True)
    (td / "turn-1.md").write_text("# RUN u1 / turn 1\nfilled in\n", encoding="utf-8")
    (td / "probes" / "turkish.py").write_text(TURKISH_PROBE, encoding="utf-8")
    scratch = nl.accept_scratch("u1")
    nl.write_accept(td, {"commands": [[sys.executable, str(td / "probes" / "turkish.py")]]}, [], scratch)
    for label, extra in (("unset", {}), ("forced cp1252", {"PYTHONIOENCODING": "cp1252"})):
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        env.update(extra)
        r = subprocess.run([sys.executable, str(td / "ACCEPT.py")], cwd=lane, env=env,
                           capture_output=True, timeout=300)
        out = r.stdout.decode("utf-8", errors="replace") + r.stderr.decode("utf-8", errors="replace")
        check(f"accept-unicode ACCEPT passes with PYTHONIOENCODING {label}",
              r.returncode == 0 and out.rstrip().endswith("ACCEPT ALL OK") and "çğışöü" in out, out)
    nl.drop_accept_scratch(None, "u1")


def config_cases(tmp: Path) -> None:
    repo = git_repo(tmp / "cfgrepo")
    check("config absent keeps the old defaults",
          nl.load_config(repo) == {"links": [], "tools": {}, "commands": [], "writable": [],
                                   "ports": None, "lint_timeout": nl.LINT_TIMEOUT})
    (repo / nl.CONFIG_NAME).write_text(
        'writable = ["logs/**"]\nports = {base = 8030, per_lane = 10}\n[lint]\ntimeout = 5\n', encoding="utf-8")
    cfg = nl.load_config(repo)
    check("config reads writable, ports and lint timeout",
          cfg["writable"] == ["logs/**"] and cfg["ports"] == (8030, 10) and cfg["lint_timeout"] == 5, repr(cfg))
    for bad in ('writable = ["../x"]\n', 'writable = ["/etc/x"]\n', 'ports = {base = 80, per_lane = 10}\n',
                'ports = {base = 8030}\n', '[lint]\ntimeout = 0\n'):
        (repo / nl.CONFIG_NAME).write_text(bad, encoding="utf-8")
        try:
            nl.load_config(repo)
            refused = False
        except SystemExit:
            refused = True
        check(f"config refuses {bad.strip()!r}", refused)


def port_cases(tmp: Path) -> None:
    repo = git_repo(tmp / "portrepo")
    cfg = {"ports": (8030, 10)}
    first = nl.allocate_ports(repo, cfg)
    root = nl.lanes_root(repo)
    (root / "a" / ".delegate-runs" / "a").mkdir(parents=True)
    (root / "a" / ".delegate-runs" / "a" / "lane.json").write_text(
        json.dumps({"port_base": 8030, "port_count": 10}), encoding="utf-8")
    second = nl.allocate_ports(repo, cfg)
    (root / "a-blind" / "verify").mkdir(parents=True)
    (root / "a-blind" / "verify" / ".blind.json").write_text(json.dumps({"port_base": 8040}), encoding="utf-8")
    third = nl.allocate_ports(repo, cfg)
    check("ports: first lane gets base", first == (8030, 10), repr(first))
    check("ports: an open lane's range is skipped", second == (8040, 10), repr(second))
    check("ports: a blind worktree's range is skipped too", third == (8050, 10), repr(third))
    check("ports: no config means no range", nl.allocate_ports(repo, {"ports": None}) is None)


def heuristic_cases() -> None:
    known = {"src/old.py", "src/pkg/mod.py", "README.md"}
    cases = [
        ('assert not (ROOT / "src" / "old.py").exists()', ["src/old.py"]),
        ('if os.path.exists("src/old.py"): fail("still there")', []),   # positive form: not detected
        ('ok = not Path("src/pkg").is_dir()', ["src/pkg/"]),
        ('if (!fs.existsSync(path.join(root, "src/old.py"))) {}', ["src/old.py"]),
        ('assert (lane / "src/old.py").exists() is False', ["src/old.py"]),
        ('assert (lane / "src/old.py").exists()', []),
        ('assert not (lane / "src/new.py").exists()', []),               # untracked path
    ]
    for source, want in cases:
        got = nl.gone_expectations(source, known)
        check(f"gone heuristic {source[:50]!r}", got == want, repr(got))
    crash = "Traceback (most recent call last):\n  File \"p.py\", line 1\njson.decoder.JSONDecodeError: Expecting value\n"
    check("crash: uncaught JSONDecodeError is a crash", nl.probe_crash(crash) is not None)
    check("crash: AssertionError is a verdict",
          nl.probe_crash("Traceback (most recent call last):\n  x\nAssertionError: wrong\n") is None)
    check("crash: FAIL lines are a verdict", nl.probe_crash("FAIL endpoint returns 404\n") is None)
    node = "TypeError: x is not a function\n    at Object.<anonymous> (C:\\p\\probe.cjs:3:1)\n"
    check("crash: node uncaught TypeError is a crash", nl.probe_crash(node) is not None)


def lint_cases(tmp: Path) -> None:
    lane = git_repo(tmp / "lintlane")
    (lane / "src").mkdir()
    (lane / "src" / "old.py").write_text("x = 1\n", encoding="utf-8")
    (lane / "src" / "keep.py").write_text("y = 1\n", encoding="utf-8")
    commit_all(lane)
    td = lane / ".delegate-runs" / "l1"
    probes = td / "probes"
    probes.mkdir(parents=True)
    (td / "lane.json").write_text(json.dumps({"mode": "worker", "port_base": 8030, "port_count": 10}),
                                  encoding="utf-8")
    spec = "# TASK l1\n## FILE WHITELIST\n- src/keep.py\n## FORBIDDEN\n- No file deletion\n"
    (td / "SPEC.md").write_text(spec, encoding="utf-8")
    (probes / "a_fail.py").write_text('import sys\nprint("FAIL not yet")\nsys.exit(1)\n', encoding="utf-8")
    (probes / "b_crash.py").write_text('import json\njson.loads("<html>")\n', encoding="utf-8")
    (probes / "c_gone.py").write_text(
        'import os, sys\nfrom pathlib import Path\nroot = Path(os.environ["AUDIT_ROOT"])\n'
        'sys.exit(0 if not (root / "src" / "old.py").exists() else 1)\n', encoding="utf-8")
    (probes / "d_ports.py").write_text(
        'import os, sys\nsys.exit(0 if os.environ.get("PROBE_PORT_BASE") == "8030" '
        'and os.environ.get("PYTHONIOENCODING") == "utf-8" else 5)\n', encoding="utf-8")
    pidfile = tmp / "grandchild.pid"
    (probes / "e_slow.py").write_text(
        "import subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n", encoding="utf-8")
    started = time.monotonic()
    problems, notes = nl.lint_lane(lane, "l1", 3)
    took = time.monotonic() - started
    text = "\n".join(problems + notes)
    check("lint flags the crashing probe", any("b_crash.py crashed" in p and "JSONDecodeError" in p
                                               for p in problems), text)
    check("lint does not flag a probe failing with a verdict", not any("a_fail.py" in p for p in problems), text)
    check("lint flags a gone-check under 'No file deletion'",
          any("c_gone.py" in p and "src/old.py" in p and "ARCHITECT DELETES" in p for p in problems), text)
    check("lint runs probes with the lane's port base and utf-8 io",
          any("d_ports.py: rc=0" in n for n in notes), text)
    check("lint skips a probe over the timeout and reports it",
          any("e_slow.py" in n and "still running" in n for n in notes) and took < 40, f"{took:.1f}s\n{text}")
    alive = False
    if pidfile.is_file():
        pid = pidfile.read_text().strip()
        if os.name == "nt":
            q = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
            alive = pid in q.stdout
        else:
            alive = subprocess.run(["kill", "-0", pid], capture_output=True).returncode == 0
    check("lint killed the slow probe's child process too", pidfile.is_file() and not alive)
    (probes / "e_slow.py").unlink()
    (probes / "b_crash.py").unlink()

    rc = nl.print_next_steps(lane, "l1", 1, lint=True, override=False, timeout=10)
    check("dispatch line withheld while a lint problem stands", rc == 3)

    (td / "SPEC.md").write_text(spec + "## ARCHITECT DELETES\n- src/old.py\n- ../outside.txt\n", encoding="utf-8")
    lines = nl.apply_architect_deletes(lane, "l1")
    check("ARCHITECT DELETES git-rms the tracked file", not (lane / "src" / "old.py").exists()
          and any("deleted src/old.py" in ln for ln in lines), repr(lines))
    check("ARCHITECT DELETES refuses a path outside the lane", any("../outside.txt" in ln and "left alone" in ln
                                                                   for ln in lines), repr(lines))
    again = nl.apply_architect_deletes(lane, "l1")
    check("ARCHITECT DELETES is idempotent", any("src/old.py already deleted" in ln for ln in again), repr(again))
    problems, notes = nl.lint_lane(lane, "l1", 10)
    check("lint is clean once the file is gone", not problems, "\n".join(problems + notes))
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = nl.print_next_steps(lane, "l1", 1, lint=True, override=False, timeout=10)
    check("dispatch line printed when the lint is clean", rc == 0 and "dispatch.py" in buf.getvalue(),
          buf.getvalue())


def marker_cases(tmp: Path) -> None:
    """Probes run only before the first dispatch; the marker lives under home, outside the lane."""
    home = tmp / "marker-home"
    home.mkdir()
    saved = {key: os.environ.get(key) for key in ("HOME", "USERPROFILE", "SERIT_TEST_SECRET")}
    os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
    try:
        lane = git_repo(tmp / "markerlane")
        (lane / "src").mkdir()
        (lane / "src" / "old.py").write_text("x = 1\n", encoding="utf-8")
        commit_all(lane)
        td = lane / ".delegate-runs" / "t1"
        probes = td / "probes"
        probes.mkdir(parents=True)
        (td / "SPEC.md").write_text("# TASK t1\n## GOAL\nCheck the dispatch marker.\n", encoding="utf-8")
        (td / "lane.json").write_text(json.dumps({"mode": "worker"}), encoding="utf-8")

        dspec = importlib.util.spec_from_file_location("dispatch_for_marker", HERE / "dispatch.py")
        dp = importlib.util.module_from_spec(dspec)
        dspec.loader.exec_module(dp)
        shouting = Path(str(lane).upper() if os.name == "nt" else str(lane))
        check("marker path is identical in new-lane.py and dispatch.py, case-blind on nt",
              nl.dispatch_marker(lane) == dp.dispatch_marker(lane) == nl.dispatch_marker(shouting)
              == dp.dispatch_marker(shouting) and home in nl.dispatch_marker(lane).parents,
              f"{nl.dispatch_marker(lane)} {dp.dispatch_marker(lane)}")

        ran = tmp / "marker-ran"
        (probes / "marker.py").write_text(
            f"from pathlib import Path\nPath({str(ran)!r}).write_text('ran')\n", encoding="utf-8")
        nl.lint_lane(lane, "t1", 10)
        check("marker absent: the probe runs during lint", ran.is_file())

        ran.unlink()
        marker = nl.dispatch_marker(lane)
        marker.parent.mkdir(parents=True)
        marker.write_text("dispatched\n", encoding="utf-8")
        (probes / "crash.py").write_text('import json\njson.loads("<html>")\n', encoding="utf-8")
        problems, notes = nl.lint_lane(lane, "t1", 10)
        check("marker present: no probe runs and the note says why",
              not ran.exists() and any("probes not run" in n and "already been dispatched" in n for n in notes),
              "\n".join(problems + notes))
        check("marker present: a crashing probe yields no problem and no crash note",
              not problems and not any("crashed" in n or "rc=" in n for n in notes), "\n".join(problems + notes))

        (td / "SPEC.md").write_text("# TASK t1\n## FORBIDDEN\n- No file deletion\n", encoding="utf-8")
        (probes / "gone.py").write_text(
            'import os, sys\nfrom pathlib import Path\nroot = Path(os.environ["AUDIT_ROOT"])\n'
            'sys.exit(0 if not (root / "src" / "old.py").exists() else 1)\n', encoding="utf-8")
        problems, notes = nl.lint_lane(lane, "t1", 10)
        check("marker present: the gone-check still reports a problem",
              any("gone.py" in p and "src/old.py" in p for p in problems), "\n".join(problems + notes))

        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            nl.drop_dispatch_marker(lane)
        check("drop_dispatch_marker removes the marker and says so",
              not marker.exists() and "removed dispatch marker" in buf.getvalue(), buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            nl.drop_dispatch_marker(lane)
        check("drop_dispatch_marker is silent when there is no marker", buf.getvalue() == "", buf.getvalue())

        for name in ("crash.py", "gone.py", "marker.py"):
            (probes / name).unlink()
        os.environ["SERIT_TEST_SECRET"] = "x"
        (probes / "environment.py").write_text(
            'import os, sys\nsys.exit(0 if "SERIT_TEST_SECRET" not in os.environ '
            'and "PATH" in os.environ else 1)\n', encoding="utf-8")
        problems, notes = nl.lint_lane(lane, "t1", 10)
        check("environment omits SERIT_TEST_SECRET and keeps PATH",
              not problems and any("environment.py: rc=0" in n for n in notes), "\n".join(problems + notes))
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    if os.name == "nt":
        check("accept root rejects non-private POSIX mode", True, "skipped on nt")
        check("accept root allows private POSIX mode", True, "skipped on nt")
    else:
        tempdir = tempfile.tempdir
        isolated = tmp / "private-root"
        isolated.mkdir()
        root = isolated / "serit-accept"
        root.mkdir(mode=0o777)
        root.chmod(0o777)
        try:
            tempfile.tempdir = str(isolated)
            try:
                nl.accept_scratch("x")
                refused = False
            except SystemExit as exc:
                refused = "not a private directory" in str(exc)
            check("accept root rejects non-private POSIX mode", refused)
            root.chmod(0o700)
            check("accept root allows private POSIX mode", nl.accept_scratch("x") == root / "x")
        finally:
            tempfile.tempdir = tempdir


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        accept_unicode(Path(tmp))
        config_cases(Path(tmp))
        port_cases(Path(tmp))
        heuristic_cases()
        lint_cases(Path(tmp))
        marker_cases(Path(tmp))
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} cases hold")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
