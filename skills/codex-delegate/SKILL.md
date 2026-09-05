---
name: codex-delegate
description: Delegate implementation work to Codex worker lanes - each in its own git worktree, several in parallel - while Claude stays the architect and chief reviewer. Route by judgment whenever a complete spec can be written; no per-session approval gate.
---

# Codex Delegation - Architect/Worker Protocol

Claude is the ARCHITECT and the CHIEF REVIEWER: it writes the spec, decides what
correct means, and judges the finished work. Codex is the WORKER: it writes the
code, runs its own small checks, and reports.

The scarce resource is Claude's context, not Codex's quota. So the win is CONTEXT
ISOLATION - the worker's file reads, reasoning, and tool output must never enter
Claude's context. Only a short structured report and the diff hunks review needs.

| | Worker (Codex) | Architect (Claude) |
|---|---|---|
| Code | writes it, in its own worktree | reads the diff, applies it to the main tree |
| Tests | writes and runs its own targeted checks | owns the acceptance bar, re-runs it independently |
| MCP | implementation operations only | observation and verification |
| Judgement | none - it reports facts | owns "is this actually right" |

The worker does mechanical correctness. Whether the result is *good* is never
delegated.

## 0. Routing - when to delegate

Delegation is YOUR routing call, made per task on merit. Installing this skill
is the standing authorization, so there is no per-session approval gate - ask
once and you will be asked to stop asking. If the user says "don't delegate",
that sticks until they say otherwise.

**The test is spec-completeness: can you write a complete SPEC.md right now -
goal, file whitelist, acceptance command - without guessing?**

- Yes, and the work is grunt work once specified -> delegate. Codex grinds
  through an explicit spec relentlessly; that is the thing it does best.
- No -> the missing piece IS the reason not to delegate. Close the gap (that is
  architect work) or do the task yourself. An unfillable spec is a feature: it
  filters out vague bug hunts before they burn a worker turn.

Counterweight, because the failure mode of a standing grant is reflex
delegation: **a delegation costs a spec and an audit.** While the main loop can
do the work faster than it can specify it, do it in the main loop. Delegating to
feel productive is spending more to get less.

Never delegated regardless of the test: architectural decisions, anything
needing design taste, auth/payments/DB schema/migrations (architect-only
surface), and trivial edits where the spec would be longer than the diff.

**What still requires the user's explicit word, per task, every time:**

- Granting an **outward-facing MCP server** (acts beyond this machine: mail,
  money, hosted services). Consent to delegation is a standing preference;
  consent to reaching outside the machine is not.
- Enabling **network access** for a lane
  (`-c sandbox_workspace_write.network_access=true`). Same reasoning, wider
  surface: an open shell with network exceeds any named MCP server.

## 0.1 Locate the scripts (once per session)

Every command below calls `$SKILL_DIR/scripts/...`. Resolve it first, because
`${CLAUDE_PLUGIN_ROOT}` is defined for the plugin loader and **not** in the
shell you run commands in - pasting it verbatim yields an empty path.

```bash
SKILL_DIR=$(find "$HOME/.claude/plugins" -maxdepth 10 -type f \
  -path '*/codex-delegate/scripts/doctor.py' 2>/dev/null \
  | sort -V | tail -1 | sed 's|/scripts/doctor.py||')
echo "${SKILL_DIR:?codex-delegate scripts not found - is the plugin installed?}"
```

Two measured corrections live in that command, both silent when wrong:

- **`-maxdepth 10`.** The installed path is 8 levels deep
  (`plugins/cache/<plugin>/<plugin>/<version>/skills/codex-delegate/scripts/`) -
  the version segment adds one, and the earlier `-maxdepth 7` returned
  **nothing**. An empty `SKILL_DIR` then fails downstream as
  `"$PY_BIN" "/scripts/doctor.py"`, which is why the `:?` guard is not
  decoration.
- **`sort -V | tail -1` instead of `-print -quit`.** The cache keeps one
  directory per installed version, and `-quit` takes whichever the filesystem
  hands over first - reproduced: with 2.4.0 and 2.5.0 both present it
  picked **2.4.0**. Running the previous version's scripts against this
  version's protocol is the kind of failure that shows up as an unrelated bug
  three steps later. Sorting by version and taking the last one is the fix; an
  install that leaves no stale directories behind (uninstall, install, remove
  the old cache dir) is the belt to that suspenders.

No wildcards on purpose: zsh (the macOS default) aborts the whole command when
a glob matches nothing. `find` has no such behaviour.

**Two zsh traps, same family, both measured in one session:**

- **Glob abort.** `rm -f findings/*.md probes/*.sh` with no `probes/*.sh` match
  cancels the *entire* command - the `.md` files stay too. Fixture files
  survived this way and contaminated a lane. Use `find <dir> -name '<pat>'
  -delete` when clearing directories.
- **No word splitting.** zsh does NOT split an unquoted variable on spaces:

  ```bash
  NEW="a.py b.py c.py"
  for f in $NEW; do cp "$f" "$WT/$f"; done   # zsh: one file named "a.py b.py c.py"
  ```

  Field cost: 5 files silently failed to copy into four lanes, and the diff
  check still went green because it compared tracked files while the missing
  ones were untracked. Use an array (`NEW=(a.py b.py)`) or `for f in a.py b.py`.
  The lesson generalises past zsh: a verification that measures the wrong thing
  is worse than none.

**Python 3.11+ is required**: the scripts import `tomllib`. Stock macOS
`/usr/bin/python3` is 3.9 and dies at the import line. Which command provides
that interpreter is never assumable either - §3 resolves `PY_BIN` by handshake
once per session, and every script call in this protocol goes through it.

## 1. Scope of action

This protocol never touches anything outside the working trees it creates. No
remote hosts, no code hosting services, no deployments. Workers never run git;
the architect uses git as an inspection and integration instrument only. The
work product lands in the MAIN tree as uncommitted changes for the user to
review - no commit step, and no rollback point between rounds unless the user
creates one. State this plainly when reporting.

The worker is bounded by isolation AND detection: its cwd is a disposable
worktree, and §6's footprint check sees everything it did there.

## 2. The lane model - one worktree per worker

A **lane** = one git worktree + one spec + one dispatched worker. Lanes replace
the old global `IN_FLIGHT` lock: the lock existed so two writers could not
collide in one tree, and a worker with its own tree has nothing to collide
with. Physical isolation instead of serialization.

Consequences, all deliberate:

- **Parallel lanes are allowed.** Fan independent slices out concurrently.
- **Disjoint whitelists are the architect's fan-out duty.** Two lanes whose
  FILE WHITELISTs overlap is a design error - fix the split, don't referee the
  crash. Shared files (barrels, entry points, type indexes) belong to NO lane;
  the architect edits them at integration time.
- **The architect keeps working in the main tree** while lanes run - but avoid
  editing files inside any live lane's whitelist, or `git apply` conflicts at
  integration and the resolution costs more than the parallelism saved.
- **Lanes see BASE_SHA, not your uncommitted changes.** If the task depends on
  uncommitted main-tree work, commit it first or the task is not lane-ready.
- **Practical ceiling ~20 concurrent workers** (RAM + provider rate limits;
  measured: a 23-lane run had 2 workers wedge on dead connections at startup).
  Default to <=4 lanes; go wider only when the task genuinely decomposes wide.
  **The tier caps in §5 bind tighter than this number and are about spend,
  not stability:** at most 3 `sol` lanes, exactly 1 `astra` lane. Only `luna`
  lanes are free to reach this ceiling.
- **Stagger spawns 2-5 s apart.** Same incident: the two wedged workers sat
  silent for 30 minutes. The stagger costs a minute; a zombie costs half an
  hour.

## 3. Preflight - once per fan-out

Run from the repository root (`git rev-parse --show-toplevel`). Everything in
this protocol is repo-root relative.

```bash
PY_BIN=""                                   # resolve the interpreter, do not assume it
for c in python3 python py; do              # being on PATH is not being able to run
  command -v "$c" >/dev/null 2>&1 || continue
  [ "$("$c" -c 'import tomllib; print("PY_OK")' 2>/dev/null)" = "PY_OK" ] \
    && { PY_BIN="$c"; break; }
done
[ -n "$PY_BIN" ] || echo "BLOCK: no runnable Python - setup, dispatch and doctor all need one"

"$PY_BIN" "$SKILL_DIR/scripts/doctor.py" --check
BASE_SHA=$(git rev-parse HEAD)                  # every lane pins to this
git check-ignore -q .delegate-runs || echo "BLOCK: .delegate-runs/ is not git-ignored - add it (one line in .gitignore) before opening lanes"
git worktree list                               # stale lanes from dead sessions?
```

**Resolve `PY_BIN` once, here, and use it at every script call site below**
(§4's `--trust`, §5's dispatch). Like `SKILL_DIR` it is session state, not a
per-command lookup. **Resolution is by OUTPUT, never by `command -v`** -
measured (Windows): `python3` is the Microsoft Store stub,
which sits *on* `PATH`, prints nothing to stdout and exits `9009`; there is no
Python behind it. The working interpreter on that machine was `python` (3.13).
`command -v python3` succeeds there and proves nothing, so the loop demands the
literal `PY_OK` string back. Same measurement bought codex-audit §1's handshake
in v2.7.1 - that fix covered the scope query only, and these three call sites
stayed bare: **the scope query survived the field run, setup and dispatch did
not.**

**The `.gitignore` line reports; it does not fix.** An earlier version appended
`.delegate-runs/` to `.gitignore` itself. Measured: the operator's
tree was deliberately clean and awaiting push, and that append would have
slipped a fourth unreviewed change into the pending commit set. A tool must not
edit the repository it is about to open lanes in - the user's diff is theirs,
and a one-line `.gitignore` edit they chose to make costs them nothing while one
they discover later costs them the whole review.

`--check` proves structure, login, config parse and the CLI version floor. It
does not prove the configured model is available to your account - that needs
`--smoke`, once after install and after every codex upgrade.

Stale lanes: a worktree under `*-lanes/` with no live dispatch process is a
leftover. Report it by path, say its changes are unintegrated, and ask before
removing. Never silently reap another session's lane.

**MCP registration is a preflight step, not a dispatch step.** If a lane needs
an MCP server: `doctor.py --list-mcp`, then `doctor.py --add-mcp <name>` NOW -
`--mcp` at dispatch only grants servers already registered; dispatch.py exits 4
on an unregistered name, after the spec is already written. Outward-facing
servers additionally need the user's word in chat, per task (§0). Grant nothing
a task does not need.

## 4. Open a lane

```bash
TASK_ID=$(date +%F)-<shortname>                    # e.g. 2026-07-26-inventory-ui
LANE="$(dirname "$PWD")/$(basename "$PWD")-lanes/$TASK_ID"   # the path open uses
"$PY_BIN" "$SKILL_DIR/scripts/new-lane.py" open --task-id "$TASK_ID" --base "$BASE_SHA"
# --mode audit|research for the sibling flows · --lane <path> for a short root
# (see the MAX_PATH note below) · run it from the MAIN tree, never from a lane
```

Set `LANE` yourself as above and check it against the `lane:` line the command
prints - everything downstream (`dispatch`, the DONE poll, `close`) is
`"$LANE"`, and an unset one silently becomes the current directory.

That is the whole scaffold: worktree at the verified base, trust entry, run
dir, changelog skeleton, PROMPT.txt. It exists because the list is six steps
with no judgment in it and each one fails quietly - measured at six
lanes in one session and six passes through it by hand. It stops at SPEC.md,
which is yours, and it will not print a dispatch line until that file exists
and is not a skeleton: a lane dispatched against an empty spec costs a whole
turn and returns a worker asking what to do.

By hand, when you need to see or vary the steps:

```bash
LANE="$(dirname "$PWD")/$(basename "$PWD")-lanes/$TASK_ID"
git worktree add --detach "$LANE" "$BASE_SHA"
test "$(git -C "$LANE" rev-parse HEAD)" = "$BASE_SHA" || echo "BLOCK: lane not at BASE_SHA"
"$PY_BIN" "$SKILL_DIR/scripts/doctor.py" --trust "$LANE"
mkdir -p "$LANE/.delegate-runs/$TASK_ID"
```

- **Verify the base.** Lanes branching from a stale base is a measured failure
  mode, and it stays silent until integration.
- **Windows: budget the path length BEFORE `worktree add`.** The sibling-dir
  convention above adds ~34 characters over the repo root (`-lanes/` plus the
  dated task id), and Windows still enforces MAX_PATH = 260 on most tooling.
  Measured: deepest tracked path 193 chars, +34 → 276, and
  `worktree add` itself failed. Check the budget, and when it does not close,
  put the lane under a short root instead - the convention is a default, not
  a contract:

  ```bash
  git ls-files | awk '{ print length }' | sort -rn | head -1   # deepest tracked path
  # if that + length of "$LANE" + 1 > 259: use a short root, e.g.
  LANE="$HOME/ual/$TASK_ID"
  ```

  (`git config core.longpaths true` frees only git; python/node inside the
  lane still hit the limit, so the short root is the fix, not the flag.)
- **Trust the lane path.** Codex asks for folder trust per exact project path;
  an untrusted cwd stalls the worker's first turn on a request dispatch.py can
  only answer emptily - it looks like a hung turn and the cause appears
  nowhere. (Measured: a field config carried four hand-added trust entries -
  this failure, patched by hand.) `--trust` writes the entry.
- **Install dependencies inside the lane** if acceptance needs them
  (`node_modules/` and friends do not come with a worktree).
- **Everything for a lane lives inside it**: `$LANE/.delegate-runs/$TASK_ID/`
  holds SPEC.md, PROMPT.txt, RAW_OUTPUT.log, FINAL.txt, ROUNDS.txt and the
  worker's `turn-N.md`. One location; removing the worktree removes all
  scaffolding - which is why closeout archives first (§9).

Write `SPEC.md` from `references/spec-template.md` - every field, truthfully.
If a field cannot be filled, the task is not delegation-ready (§0).

Build `PROMPT.txt`: the full contents of `references/worker-contract.md`,
followed by one line:

```
Read .delegate-runs/<task-id>/SPEC.md and execute it. Task dir: .delegate-runs/<task-id>/ - this is turn <N>; your changelog skeleton is at .delegate-runs/<task-id>/turn-<N>.md - fill it in.
```

**Seed the changelog skeleton before every dispatch** - worker, review, retry
and recovery alike:

```bash
printf '# RUN %s / turn <N>\nstatus: SKELETON - worker has not filled this in\n' \
  "$TASK_ID" > "$LANE/.delegate-runs/$TASK_ID/turn-<N>.md"
```

**And make the acceptance command check it.** The spec's ACCEPTANCE wrapper
opens with:

```bash
CL=".delegate-runs/<task-id>/turn-<N>.md"
if [ ! -s "$CL" ] || grep -q SKELETON "$CL"; then
  echo "ACCEPTANCE: changelog $CL missing or not filled in" >&2; exit 1
fi
```

(Verified in all four states - missing, empty, skeleton, filled - under both
bash and zsh, with and without `set -e`. Written as an `if` rather than an
`||`/`&&` chain on purpose: the chain form is correct but depends on operator
precedence that reads wrong at a glance, and this line is meant to be copied.)

Why both a seeded file and an acceptance gate, in escalating order of force:
prose did not bind (a "hard deliverable, never optional" changelog was written
in **1 lane out of 6**, and the miss survived extra emphasis in the prompt).
The seeded skeleton made it a slot to fill rather than a rule to remember. The
acceptance gate is what makes skipping it *fail the worker's own loop* - the
worker runs acceptance itself, sees red, and writes the changelog before it can
finish. A rule that only the architect enforces is discovered after the turn is
over; one inside acceptance is enforced during it. §6 keeps the same check as
the architect's independent verdict.

**You own the turn counter.** Every dispatch is a cold start - the worker has
no memory of earlier turns and cannot know N. Before each retry, rewrite the
instruction line with the new N, seed the new turn's skeleton, and confirm
`turn-<N-1>.md` is still on disk. All three at once:

```bash
"$PY_BIN" "$SKILL_DIR/scripts/new-lane.py" turn --lane "$LANE" --task-id "$TASK_ID" --turn <N>
# --recovery swaps in the recovery instruction line (§10)
# refuses when turn-<N-1>.md is absent, or when turn-<N>.md is already filled in
```

## 5. Dispatch

```bash
"$PY_BIN" "$SKILL_DIR/scripts/dispatch.py" \
  --task-dir "$LANE/.delegate-runs/$TASK_ID" \
  --repo "$LANE" \
  --prompt-file "$LANE/.delegate-runs/$TASK_ID/PROMPT.txt" \
  --done-file "$LANE/.delegate-runs/$TASK_ID/DONE" \
  --timeout 3600
  # --mcp <name>          per granted server, registered in §3
  # --sandbox read-only   for review lanes
  # --model <id>          per lane - overrides the config default, see below
  # --effort <level>      per lane - low|medium|high|xhigh|max, default high
```

**Two dials, and the saving comes from choosing which one to cut.** Model and
reasoning effort are set per lane and the flags override whatever
`~/.codex-worker/config.toml` holds, so tiering never means editing config
between lanes. Route on **how hard the lane is**, then cap the fan-out by the
tier you picked - the cap is half the rule, not a footnote to it.

| Lane difficulty | Model | Effort | Parallel lanes |
|---|---|---|---|
| **Basic** - writing or reviewing code that rides an existing pattern end to end | `gpt-5.6-luna` | `max` | as many as the work decomposes into; cost places no cap here |
| **Middling** - work that pushes back, ordinary review of real logic | `gpt-5.6-sol` | `high` when it is the only sol lane, `medium` as soon as a second one opens | **max 3** |
| **Hardest** - core seams, the largest surfaces, anything whose defect would live in production unnoticed | `gpt-6-astra` | `high` | **1**, unless the user says otherwise |

(Tiering set by Burak, 5 Sep 2026; it supersedes the earlier two-tier rule of
cheap-at-max plus expensive-at-medium, which had no fan-out term at all.)

**Parallelism is the dial that ends the usage window, not the model name.**
Six lanes on `sol` at `high` exhausts the limit outright - which is why the sol
row caps at three lanes and drops to `medium` the moment a second sol lane
opens, and why astra runs alone. Luna at `max` is the one tier you can fan out
freely on cost grounds; §2's default of <=4 lanes still applies to it, for
wedge risk rather than for spend.

"Hardest" is decided by the cost of being wrong, not by the size of the work:
silent data loss, an authorization or privacy boundary, a termination contract.
Style, dead code, test coverage and documentation consistency go to the luna
row no matter how many files they touch.

Measured under the earlier two-tier rule, and the shape still holds: a
five-lane audit with two lanes on the expensive model at medium and three on
the cheap model at max sat at **56% of a five-hour usage window**, where
running every lane on the expensive model would have exhausted it before the
audit finished. In the same run the cheap lanes produced three to four times
the transcript of the expensive ones and still cost less - **transcript volume
is not a proxy for spend**, and reasoning from one to the other gets the tier
backwards.

**Confirm the pairing before you rely on it, because a bad one fails
silently.** Not every model offers every effort level, and dispatch.py drops a
turn whose effort the model does not support as `turn/failed` - the reason
stays in `RAW_OUTPUT.log` and nothing else says a word. List what the account
actually has (`app-server`'s `model/list`, under the worker's `CODEX_HOME`)
rather than assuming the levels carry across models. Measured 5 Sep 2026 on
codex-cli 0.153.4: `gpt-6-astra` and `gpt-5.6-sol` accept `low` through
`ultra`; `gpt-5.6-luna` stops at `max` and has no `ultra`. Every pairing in the
table above was checked against that list - re-check it after a CLI upgrade
rather than inheriting this line.

Run it in the background; the harness wakes you when it exits. Start the next
lane 2-5 s later (§2). On macOS prefix with `caffeinate -i` - best-effort only:
it blocks idle sleep, not a closed lid, so it never replaces the liveness check.

**When the harness does not wake you** - a detached `nohup`, a background
`Start-Process`, a lane launched from another session - `--done-file` is the
completion signal. Poll the file, not the process table:

```bash
D="$LANE/.delegate-runs/$TASK_ID/DONE"
rm -f "$D"                                    # BEFORE launching: see below
while [ ! -f "$D" ]; do sleep 20; done
cat "$D"                                      # rc=<n> verdict=<...> final=<path>
```

```powershell
# same rule, PowerShell: the marker is the signal, not the process table
$D = "$LANE\.delegate-runs\$TASK_ID\DONE"
Remove-Item $D -ErrorAction SilentlyContinue   # BEFORE launching
while (-not (Test-Path $D)) { Start-Sleep 20 }
Get-Content $D
```

**Clear it before you launch, not after.** dispatch.py deletes a stale marker
too, but it does so ~100 ms into its own startup, and a `[ -f ]` test takes
about a millisecond: measured, a poll started right after `nohup ... &`
returned on its first iteration holding the PREVIOUS turn's rc and verdict.
`new-lane.py turn` clears it when it seeds the turn, which is the same fix one
step earlier.

It is written however the turn ends - success, failure, timeout, a rejected
argument, or an unhandled crash - so the poll cannot outlive the run. Without
it every lane grows its own watcher loop: measured at six lanes and
six loops in one session, none of them agreeing on what to look for.

**Round bookkeeping lives on disk, not in your context.** Before every dispatch
(worker, review, or retry) append one line to the lane's `ROUNDS.txt`:

```
<ISO-8601> | worker|review|retry | turn <N> | signature: <first line of the failure, or ->
```

Caps are counted from this file, not from memory: max 2 review rounds, max 2
architect retries per lane. **Spin** = two consecutive lines with the same
signature -> stop the lane and ask the user. After a context compaction, read
ROUNDS.txt before doing anything else.

**Liveness is read from the trace, not the process table.** A lane whose
RAW_OUTPUT.log has not grown for many minutes while its dispatch process still
runs is wedged regardless of what `ps` says. dispatch.py kills the worker at
`--timeout` and exits non-zero, so the ceiling is enforced - but check log
growth when a lane feels slow instead of waiting the timeout out.

**Write commands the approval filter can approve.** dispatch.py judges every
command argv token by token and declines anything that names a location
outside the lane - absolute paths, home references (`~`, `$HOME`,
`%USERPROFILE%`), and location variables (`%TEMP%`, `$env:TEMP`, `%APPDATA%`,
`%LOCALAPPDATA%`, `%PROGRAMDATA%`, `%PUBLIC%`). The filter is deliberately
blunt, so the brief has to meet it. Three rules, each one a measured false
positive - a lane that a filter had been starving across several rounds ran
**23 approvals, 0 declines, rc=0** once the brief carried them:

1. **Build fixtures inside the lane**, at `.delegate-runs/<lane>/fixtures/`.
   A test tree created with `tempfile.mkdtemp()` lands in the system temp
   directory, which is outside the lane, and every path derived from it is
   declined.
2. **Put container paths inside the program text, not in argv.** A container
   path handed over as its own argument (`docker run ... /workspace/case`) is
   indistinguishable from a host absolute path; pass it inside the `-c`
   program string instead, where it is data rather than an operand.
3. **No argument may begin with `/` or `\`.** This one bites where you least
   expect it: the regex fragments `'\(e\)|return'` and `'\{'` were read as a UNC
   path and declined. Anchor patterns differently, or pass them through a
   file.

The real cure belongs in the tool, and is not there yet: the filter cannot
know that argv after `docker` belongs to another namespace, and cannot tell a
regex from a path. Until it can, the brief carries that burden - which is why
these three lines belong in the brief itself, not in a troubleshooting page
read after a round has already been starved.

**Check dispatch.py's exit code BEFORE reading FINAL.txt** - and read `5` as
its own case, not as one more failure:

| exit | what happened | FINAL.txt holds |
|---|---|---|
| `0` | turn completed, nothing declined | the worker's report |
| `5` | turn completed, but approvals were **declined** - the worker was starved, not refused | a real report, ending `--- dispatch: BLOCKED-BY-APPROVALS (n approvals declined) ---` |
| `1` | the turn failed, timed out, or the provider refused it | `DISPATCH FAILED: <reason>` - unless it died before the turn began (unreadable prompt file, no codex CLI), where there is no FINAL.txt and the reason is on stderr |
| `2` `3` `4` | preflight: bad argument, toolchain too old, unregistered MCP server | the turn never started. A rejected *argument* dies before FINAL.txt is touched, so last round's report may still be sitting there - trust the DONE marker's `verdict=NO-TURN`, not the file |

`1` is the case that sends you to §10: read the last ~40 lines of
RAW_OUTPUT.log for the cause, and never treat a stale report as this round's
result. `5` is the opposite - the report is real and must be read, because the
turn ran to completion with permissions it needed denied, and what it produced
is whatever survived that. The blanket rule that used to sit here ("non-zero
means the turn never completed") predates exit 5 and would have you discard a
finished turn; the grep for `^\[decline\]` in RAW_OUTPUT.log tells you what it
was denied.

**A provider can refuse a turn, and the refusal arrives as a completion.**
`turn/completed` carries a `status` and, when it failed, `error.codexErrorInfo`.
Measured: OpenAI's cybersecurity classifier rejected a red-team turn
(`status: failed`, `codexErrorInfo: cyberPolicy`) in **1 of 4 lanes**, and the
wrapper still reported `OK` because it only checked that a final message
existed - an empty lane delivered as a finished one. dispatch.py now inspects
those fields and exits non-zero, so the exit-code rule above covers this case
too. Expect it occasionally on red-team briefs: it is a policy refusal, not a
bug to work around, and a lane that returns zero findings is worth a look at
`RAW_OUTPUT.log`'s tail before it is believed.

## 6. Verify a lane before trusting it

The worker's report is a claim. The lane started clean at BASE_SHA, so the
checks are direct - no baseline diffing, no attribution puzzles:

```bash
test -f "$LANE/.delegate-runs/$TASK_ID/turn-<N>.md" \
  && ! grep -q 'SKELETON' "$LANE/.delegate-runs/$TASK_ID/turn-<N>.md" \
  || echo "BLOCK: changelog missing or unfilled -> turn UNTRUSTED"
git -C "$LANE" rev-parse HEAD                          # must still equal BASE_SHA
git -C "$LANE" status --porcelain -uall -- . ':(exclude).delegate-runs'
```

That status output IS the footprint: the lane was pristine, so every listed
path is the worker's work, untracked files included (`-uall` matters - without
it a planted file inside a new directory is invisible; measured). Every path
must appear in the spec's FILE WHITELIST; any extra is a scope violation - stop
the lane and report. A moved HEAD means the worker ran git despite contract
rule 1: discard the turn as untrusted.

**Build byproducts are not violations.** The footprint command respects the
repo's `.gitignore`; if a regenerable artifact still shows up (`__pycache__/`,
`dist/`, coverage files - measured live on the first lane run), the gitignore
is missing an entry, and both the worker's acceptance runs AND yours produce
the artifact. Judge it for what it is: never integrate it, never count it
against the worker, and flag the gitignore gap in the §9 report.

Then run the spec's ACCEPTANCE command yourself, **inside the lane**. The exit
code is the verdict, not the worker's claim about it.

**Workers never run the repo-root full gate** - only their spec's targeted
acceptance. The full gate belongs to the main tree after integration (§8): a
lane's green is provisional by construction, since no lane can see the others.

## 7. Review - cheap layers first

**L0 - worker self-loop (free).** The worker runs its acceptance itself and
fixes, max 5 attempts. Judge it by outcome, not its self-reported attempt count.

**L1 - Codex reviewer (free).** A second dispatch, in the SAME lane, read-only:

- `--task-dir "$LANE/.delegate-runs/$TASK_ID-review"` - its own dir, so it
  cannot overwrite the worker's FINAL.txt or RAW_OUTPUT.log.
- `--sandbox read-only`, no `--mcp`.
- PROMPT.txt = full `references/review-protocol.md` + one line:
  `Review the current working tree against .delegate-runs/<task-id>/SPEC.md - it holds the GOAL, FILE WHITELIST and BASE_SHA. Do not modify anything.`

After it returns, re-run §6's status command: **a reviewer's footprint must be
EMPTY.** A review lane that changed anything is itself the most serious finding
of the round - the read-only sandbox did not hold. On `request-changes`, relay
the findings verbatim into a retry; you are a courier at this layer, not a
judge. Max 2 rounds, counted in ROUNDS.txt. A confident, evidence-free approval
is the most expensive thing this protocol can produce - that is why the
reviewer's `CHECKED:` line is mandatory.

**L2 - runtime verification (the architect's own eyes).** A green acceptance
command proves compilation and targeted checks, not that the feature works. For
anything observable - UI, gameplay, rendering - Claude verifies it in the
running application through its own MCP access, after integration, and says
plainly what it saw. Work is done because someone looked.

**L3 - architect audit.** `git -C "$LANE" diff --stat`, then hunks for files
the reviewer flagged or the spec marked risky, then every untracked file in the
footprint (new files never appear in a diff). Never the full diff by default.

**Classify before re-dispatching:** spec wrong -> fix SPEC.md, retry. Spec
right, implementation wrong -> narrow correction. Acceptance command itself
wrong -> fix the command, not the code. Genuinely hard -> split the task or do
it yourself.

## 8. Integrate - the architect applies the diff

The transfer is the diff, not a prose summary: re-typing 400 lines from a
description is lossy, and reading the diff buys the same accountability at a
fraction of the cost.

**Order is dependency order, never completion order.** Decide it before
applying anything; the lane that finished first goes first only if nothing
depends on it.

Per lane, from the main repository root:

```bash
git -C "$LANE" diff --binary > "$LANE/.delegate-runs/$TASK_ID/lane.patch"
git apply --check "$LANE/.delegate-runs/$TASK_ID/lane.patch"   # dry-run first
git apply         "$LANE/.delegate-runs/$TASK_ID/lane.patch"
# untracked files from the §6 footprint: copy each in, per whitelist -
# they are NOT in the patch, and forgetting them ships half a lane.
```

- **Read the diff before applying it.** You are accountable for what enters the
  main tree; reading is the accountability.
- `git apply --check` fails -> the main tree moved under the lane, or lanes
  overlapped. Resolving that is judgment work - yours, in the main loop.
- **After ALL lanes are applied: run the full gate once in the main tree**
  (install -> build -> typecheck -> test, whatever the project defines). A red
  gate means nothing is reported as done. This is the only gate that counts;
  every lane-local green was provisional.

## 9. Closeout - per lane, never skipped

Steps 1 and 2 are one command - the same script that opened the lane closes it:

```bash
"$PY_BIN" "$SKILL_DIR/scripts/new-lane.py" close --lane "$LANE" --task-id "$TASK_ID"
# archives, removes the worktree, prunes, drops the trust entry.
# It refuses while the lane holds changes outside .delegate-runs/ - integrate
# them first, or pass --force if they are genuinely disposable.
```

1. **Archive the contract, then verify the archive, then delete.** The order
   binds, and the middle step is the one that gets skipped: `close` copies
   `SPEC.md`, `turn-*.md`, `FINAL.txt`, `ROUNDS.txt` and the findings tree to
   `<main-repo>/.delegate-runs/ARCHIVE/<task-id>/`, compares every copy against
   its source by sha256, writes `MANIFEST.sha256`, and only then removes the
   worktree. A mismatch stops the removal and leaves the lane intact.

   The step exists because a copy that silently did not happen looks exactly
   like one that did, and the next command is irreversible: in one field round
   nine lanes were removed with their outputs uncopied, and every proof script
   and finding text in them is unrecoverable. Existence alone is not the test
   either - a truncated destination passes that - so the check compares
   content.

   **`.delegate-runs/` is gitignored, so an empty-looking lane can be full.**
   `git status --porcelain` in a lane says nothing about the run directory,
   which is exactly where the archive's contents live. Never read a clean
   status as "there is nothing to archive here".
2. **Remove the worktree and its trust entry:**
   `git worktree remove --force "$LANE"`, then `git worktree prune`, then
   `doctor.py --untrust "$LANE"`. Not optional, not deferrable: accumulated
   worktrees are a measured failure (9 stale worktrees + 8 branches in one
   field case), trust entries for deleted paths pile up the same way, and
   every §3 preflight will nag about leftovers.
3. **Report in chat:** what changed, YOUR acceptance result per lane, the full
   gate result, what you observed at L2, every `uncertain:` flag from the
   changelogs, any scope violation. The changes sit uncommitted for the user.

Abandoned lane: archive as above, note the work is unintegrated, remove the
worktree only after the user confirms.

## 10. Recovery

One path. A fresh dispatch into the same lane, same PROMPT.txt, but this
instruction line:

`Read .delegate-runs/<task-id>/SPEC.md. Assess the current working tree against it and complete what is missing. This is turn <N>; your changelog skeleton is at .delegate-runs/<task-id>/turn-<N>.md - fill it in.`

Seed the skeleton first, as always (§4).

The conversation is disposable; the lane and its SPEC.md are the state. A fresh
worker reading the real tree beats a resumed one trusting its memory. Lanes
survive session death - `git worktree list` finds them (§3).

If dispatch.py died leaving a worker behind: `pgrep -fl 'codex app-server'`,
kill what you started, then re-dispatch. Never reap processes you did not spawn
without inspecting them first.

## Research tasks

Tasks whose deliverable is a report, not code, follow
`references/research-task.md`. They usually need no lane at all - a scratch
directory outside any repository, or a read-only dispatch. The variant file
states exactly what changes; anything not listed there is unchanged.

## Reference files

- `references/spec-template.md` - mandatory SPEC.md fields
- `references/worker-contract.md` - worker standing contract (verbatim into PROMPT.txt)
- `references/review-protocol.md` - reviewer contract
- `references/research-task.md` - variant for report-producing tasks
- `references/setup.md` - environment setup and troubleshooting

## Scripts

All four live in `$SKILL_DIR/scripts/` and run under `"$PY_BIN"` on both
platforms. None of them makes a decision that belongs to the architect.

- `doctor.py` - preflight, worker home, trust entries, MCP handover
- `new-lane.py` - `open` / `turn` / `close`: the lane scaffold and its cleanup
- `dispatch.py` - one worker turn, with `--done-file` as the completion signal
- `run-probes.py` - run audit probes, verify the runner and the root, one verdict table
