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

Delegation is YOUR routing call, made per task on merit. There is no per-session
approval gate: the user granted a standing authorization (2026-07-26). If the
user says "don't delegate", that sticks until they say otherwise.

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
  `python3 "/scripts/doctor.py"`, which is why the `:?` guard is not decoration.
- **`sort -V | tail -1` instead of `-print -quit`.** The cache keeps one
  directory per installed version, and `-quit` takes whichever the filesystem
  hands over first - reproduced here: with 2.4.0 and 2.5.0 both present it
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

**Python 3.11+ is required**: both scripts import `tomllib`. Stock macOS
`/usr/bin/python3` is 3.9 and dies at the import line.

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
- **Stagger spawns 2-5 s apart.** Same incident: the two wedged workers sat
  silent for 30 minutes. The stagger costs a minute; a zombie costs half an
  hour.

## 3. Preflight - once per fan-out

Run from the repository root (`git rev-parse --show-toplevel`). Everything in
this protocol is repo-root relative.

```bash
python3 "$SKILL_DIR/scripts/doctor.py" --check
BASE_SHA=$(git rev-parse HEAD)                  # every lane pins to this
git check-ignore -q .delegate-runs || echo '.delegate-runs/' >> .gitignore
git worktree list                               # stale lanes from dead sessions?
```

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
LANE="$(dirname "$PWD")/$(basename "$PWD")-lanes/$TASK_ID"
git worktree add --detach "$LANE" "$BASE_SHA"
test "$(git -C "$LANE" rev-parse HEAD)" = "$BASE_SHA" || echo "BLOCK: lane not at BASE_SHA"
python3 "$SKILL_DIR/scripts/doctor.py" --trust "$LANE"
mkdir -p "$LANE/.delegate-runs/$TASK_ID"
```

- **Verify the base.** Lanes branching from a stale base is a measured failure
  mode, and it stays silent until integration.
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
`turn-<N-1>.md` is still on disk.

## 5. Dispatch

```bash
python3 "$SKILL_DIR/scripts/dispatch.py" \
  --task-dir "$LANE/.delegate-runs/$TASK_ID" \
  --repo "$LANE" \
  --prompt-file "$LANE/.delegate-runs/$TASK_ID/PROMPT.txt" \
  --timeout 3600
  # --mcp <name>          per granted server, registered in §3
  # --sandbox read-only   for review lanes
```

Run it in the background; the harness wakes you when it exits. Start the next
lane 2-5 s later (§2). On macOS prefix with `caffeinate -i` - best-effort only:
it blocks idle sleep, not a closed lid, so it never replaces the liveness check.

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

**Check dispatch.py's exit code BEFORE reading FINAL.txt.** Non-zero means the
turn never completed; FINAL.txt then contains `DISPATCH FAILED: <reason>`. Read
the last ~40 lines of RAW_OUTPUT.log for the cause and go to §10 - never treat
a stale report as this round's result.

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

1. **Archive the contract:** copy `SPEC.md`, `turn-*.md`, `FINAL.txt`,
   `ROUNDS.txt` to `<main-repo>/.delegate-runs/ARCHIVE/<task-id>/`. Until the
   user reviews the uncommitted diff, the spec is the only record of what was
   sanctioned - deleting it with the worktree orphans the diff.
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
