---
name: codex-delegate
description: Delegate implementation work to a Codex worker while Claude stays the architect and reviewer. Opt-in only - never start delegating without explicit user approval in this session. Use when the user invokes /codex-delegate, or ask once before the first delegation of a session.
---

# Codex Delegation - Architect/Worker Protocol

Claude is the ARCHITECT and the CHIEF REVIEWER: it writes the spec, decides what
correct means, and judges the finished work. Codex is the WORKER: it writes the
code, runs its own small checks, and reports.

The scarce resource is Claude's context, not Codex's quota. So the win is CONTEXT
ISOLATION - the worker's file reads, reasoning, and tool output must never enter
Claude's context. Only a short structured report and targeted diff hunks do.

The division that matters:

| | Worker (Codex) | Architect (Claude) |
|---|---|---|
| Code | writes it | reads only what review needs |
| Tests | writes and runs its own unit checks | writes the acceptance bar, re-runs it independently |
| MCP | implementation operations only | observation and verification |
| Judgement | none - it reports facts | owns "is this actually right" |

The worker does mechanical correctness. Whether the result is *good* is never
delegated.

## 0.1 Locate the scripts first (do this once per session)

Every command in this document calls `$SKILL_DIR/scripts/...`. Resolve it before
the first one, because `${CLAUDE_PLUGIN_ROOT}` is defined for the plugin loader
and **not** in the shell you are about to run commands in - pasting it verbatim
gives you an empty path and a confusing "No such file or directory".

```bash
SKILL_DIR=$(find "$HOME/.claude" -maxdepth 6 -type f \
  -path '*/codex-delegate/scripts/doctor.py' -print -quit 2>/dev/null \
  | sed 's|/scripts/doctor.py||')
echo "${SKILL_DIR:?codex-delegate scripts not found under ~/.claude}"
```

No wildcards on purpose. **zsh is the default shell on macOS and aborts the whole
command when a glob matches nothing** (`no matches found`), which is exactly what
happens on a plugin-only or skill-only install - the pattern for the other shape
matches nothing and takes the command down with it. `find` has no such behaviour
and this form is identical under bash and zsh.

Both install shapes are supported: as a plugin the scripts sit under
`.../codex-delegate/<version>/skills/codex-delegate/scripts/`, as a plain user
skill under `~/.claude/skills/codex-delegate/scripts/`. If both are present they
can drift apart - `diff -r` them and delete the stale one.

**Python 3.11+ is required for the scripts in this document**: both import
`tomllib`, which does not exist before 3.11, and stock macOS `/usr/bin/python3`
is 3.9.

```bash
python3 -c 'import sys,tomllib; print(sys.version.split()[0])' \
  || echo "need Python 3.11+ (try python3.11/3.12/3.13, or brew install python)"
```

This applies to `doctor.py` and `dispatch.py` **only**. Do NOT carry that
interpreter into the spec's ACCEPTANCE command: the project's test runner may
live in a different Python, and pinning the wrong one makes acceptance fail for a
reason that has nothing to do with the worker. Pick ACCEPTANCE's interpreter from
the project, and prove it runs before dispatching (spec-template, "can it pass?").

## 0. Activation gate (MANDATORY, once per session)

This skill is never a default behaviour.

- User invoked `/codex-delegate` -> approved for this session.
- Otherwise, before the FIRST delegation, ask once and wait: "This looks like a
  good fit for delegating to Codex. Shall I use codex-delegate this session?"
- Declined -> do not delegate for the rest of the session, do not ask again, do
  the work yourself.
- Approval is per session and does not carry into a new one.

## 1. Scope of action

This protocol never touches anything outside the working tree. No remote hosts,
no code hosting services, no deployments. Claude uses git as an inspection
instrument only (`status`, `diff`, `rev-parse`); the work product stays
uncommitted for the user to review. If the user wants any of that done, they will
ask, and it is a separate request handled outside this skill.

State this plainly when reporting: there is no rollback point between rounds. If
the user wants one, they create it.

The worker is under the same limit, enforced by DETECTION rather than by
sandboxing - it has a shell and could run anything. The §6 checks exist precisely
because the boundary is not structurally guaranteed.

## 2. Delegation threshold

Delegate meaningful vertical slices: a feature across its layers, a module's test
suite, a mechanical multi-file refactor with crisp rules. Few large tasks beat
many small ones - every delegation costs Claude a spec and an audit, while
waiting on the worker is free.

**Research also delegates well**, and for the same reason: a worker that reads a
binary, a protocol schema and three prior implementations spends its own context
doing it, and returns a report. If the deliverable is findings rather than
product code, follow `references/research-task.md` - the git baseline, the
acceptance command and the closeout step all change, and one of them backfires if
you do not. Everything else in this protocol is unchanged.

Do NOT delegate:
- Architectural decisions, or anything needing design taste
- Vague bug hunts - if you cannot write the spec, you cannot delegate it, and
  that filter is a feature
- Trivial edits (under ~30 lines) - the overhead exceeds the win
- Auth, payments, DB schema, migrations - architect-only surface
- Anything whose file whitelist you cannot enumerate precisely

## 3. Preflight

```bash
find .delegate-runs -maxdepth 2 -name IN_FLIGHT 2>/dev/null   # must print NOTHING
python3 "$SKILL_DIR/scripts/doctor.py" --check                # worker home + login
git rev-parse HEAD                                            # record as BASE_SHA
find .delegate-runs -mindepth 1 -maxdepth 1 -type d 2>/dev/null  # dirs older than
                                                # ~3 days are abandoned -> ask, then clean
```

Glob-free again, and for the same reason as §0.1: under zsh
`ls .delegate-runs/*/IN_FLIGHT 2>/dev/null` fails loudly with `no matches found`
in the **normal** case - a repo with no runs in flight. The redirect does not
suppress it, because the error comes from the shell's expansion, not from `ls`.
Judge these by their output, not by their exit status.

Run `--check` BEFORE writing the spec. It costs no model tokens, and a broken
worker login otherwise surfaces only after the spec is written and dispatched.

A dirty working tree is FINE and expected. Instead of demanding a clean tree,
snapshot the pre-existing dirty state as a baseline and let §6 isolate the
worker's footprint by diffing against it:

```bash
mkdir -p .delegate-runs/<task-id>
git status --porcelain -uall > .delegate-runs/<task-id>/BASELINE.txt
```

`mkdir -p` first: the directory is defined in §4, which comes later, and without
it this line fails with "No such file or directory".

**`-uall` is not optional.** Plain `git status --porcelain` collapses an entire
untracked directory into one line, so anything the worker writes *inside* an
untracked directory is invisible to the §7 footprint diff. Verified: a file
planted at `.delegate-runs/some-other-run/STOLEN.txt` - a DO-NOT-TOUCH violation -
produced no diff at all without `-uall`, and showed up immediately with it. Use
the same flag in both places or the comparison is meaningless.

Abort only on an existing IN_FLIGHT lock.

## 4. Create the task

```
.delegate-runs/<task-id>/     # task-id: YYYY-MM-DD-shortname
    SPEC.md                   # durable contract - references/spec-template.md
    BASELINE.txt              # pre-existing dirty state, written in §3
    PROMPT.txt                # worker contract + the one-line instruction
    IN_FLIGHT                 # exists from dispatch until closeout
    RAW_OUTPUT.log            # transcript - never read this on the happy path
    FINAL.txt                 # the worker's final report - read THIS
    turn-N.md                 # worker changelogs
```

Fill every field of `references/spec-template.md`. If a field cannot be filled
truthfully, do NOT delegate - close the gap first. An unfillable spec means the
task is not delegation-ready.

Build `PROMPT.txt` as the full contents of `references/worker-contract.md`,
followed by one line:

```
Read .delegate-runs/<task-id>/SPEC.md and execute it. Task dir: .delegate-runs/<task-id>/ — this is turn <N>; write your changelog to .delegate-runs/<task-id>/turn-<N>.md
```

**You own the turn counter.** Every dispatch is a cold start: `dispatch.py` opens
a new thread, so the worker has no memory of earlier turns and cannot work out
its own number. The contract tells it to write `turn-<N>.md` and §7 checks for
exactly that file, so if you do not put N in the instruction line the worker
writes `turn-1.md` every round - overwriting the only durable record of what the
previous round did, and making §7 fail on work that was correct.

Before each retry: bump N in that last line, and confirm `turn-<N-1>.md` is still
on disk before dispatching.

Disk is the durable state. Never architect around a session id; always architect
around SPEC.md.

## 5. Choosing the worker's MCP servers

Ask yourself: *if I were doing this task myself, which MCP would I reach for?*
That is the one the worker needs. Name it in the spec's MCP field and pass it as
`--mcp <name>`. A Unity task gets the Unity server; a mail task gets the mail
server; a pure refactor gets none.

Two limits:

- **Grant nothing else.** Name only what the task needs.

  Know what `--mcp` actually does, though: it sets
  `default_tools_approval_mode: approve` for the servers you name. It does **not**
  switch the others off. Every server registered in the worker's
  `~/.codex-worker/config.toml` still starts with the thread - verified: with no
  `--mcp` at all, registered servers came up and reached `ready`. So registration
  is the real grant, and `doctor.py --add-mcp` is the decision point, not dispatch
  time. Keep the worker's config minimal; do not register a server you are not
  prepared to hand over on some future task.
- **Outward-facing servers need the user's word, per task.** A server that acts
  beyond this machine - sends mail, moves money, writes to a hosted service -
  must be named to the user in chat and approved before dispatch. Never hand one
  over silently. `doctor.py --list-mcp` marks these `blocked`.

The worker uses MCP to BUILD. Observation stays with the architect: entering play
mode, taking screenshots, profiling, or any "does this look right" check is §8's
job, and the spec must forbid the worker from state-changing operations it was
not explicitly granted. Side effects that are not files are invisible to the §6
footprint check, so the way to control them is to not authorise them.

## 6. Dispatch and lock

`$SKILL_DIR` below is wherever this skill is installed - see §0.1. Run from the
repository root: every `.delegate-runs/...` path here is relative to it, and
`--repo "$PWD"` is what the worker gets as its working directory.

```bash
touch .delegate-runs/<task-id>/IN_FLIGHT
python3 "$SKILL_DIR/scripts/dispatch.py" \
  --task-dir .delegate-runs/<task-id>/ \
  --repo "$PWD" \
  --prompt-file .delegate-runs/<task-id>/PROMPT.txt \
  --mcp <server>            # repeat per granted server; omit when none
```

Run it in the background: the harness wakes you when it exits, so do not poll.

**The lock is held until closeout (§9), NOT until the first result.** While
IN_FLIGHT exists Claude must not write to the working tree, must not use the MCP
servers granted to the worker, and must not start a second **implementation**
delegation. Reading, planning, reviewing and talking to the user are all fine.
This survives context compaction, which is the point.

The §8 L1 review run is the single exception to the one-delegation rule: it is
read-only, gets no MCP grant, and **must use its own task dir**
(`.delegate-runs/<task-id>-review/`) so it cannot overwrite the worker's
`FINAL.txt` or `RAW_OUTPUT.log`.

**Check the exit status before reading anything.** `dispatch.py` exits non-zero
when the turn never completed - timeout, protocol error, worker crash, codex not
on PATH. On non-zero, `FINAL.txt` contains `DISPATCH FAILED: <reason>` instead of
a report: tell the user what happened, read the tail of `RAW_OUTPUT.log` (the
app-server's stderr is interleaved there and usually names the cause), and go to
§10. Do not treat any other file on disk as this round's result.

On success `dispatch.py` writes the transcript to `RAW_OUTPUT.log` and the
worker's final report to `FINAL.txt`. Read `FINAL.txt`. The transcript exists for
forensics.

## 7. Verify before trusting

The worker's report is a claim. The observed failure mode is a worker reporting a
changelog path it never wrote - so check the disk first, always, even when the
code looks right.

```bash
test -f .delegate-runs/<task-id>/turn-<N>.md   # missing -> UNTRUSTED, turn failed
git rev-parse HEAD                             # must equal BASE_SHA
git diff --cached --stat                       # must be empty
git status --porcelain -uall                   # footprint check vs BASELINE.txt
```

**Footprint check.** Diff the current `git status --porcelain` against
`BASELINE.txt`. Every path that is new, or whose status changed, is the worker's
footprint and must appear in the spec's FILE WHITELIST. A file that was clean at
preflight and is now touched but is not whitelisted is a scope violation: stop
and report. Files already dirty at preflight are pre-existing noise, but flag any
that are not whitelisted - the worker could have piggybacked on them and you
cannot fully attribute them.

**No repository?** The footprint check is the one safety mechanism you cannot
simply drop. When the project is not under git - common for research tasks -
substitute the mtime window in `references/research-task.md` §1. It attributes
the worker's writes just as well; it only takes one extra line at preflight.

Then run the acceptance command YOURSELF. The exit code is the verdict, not the
worker's claim about it.

## 8. Review - cheap layers first

**L0 - worker self-loop (free).** The worker runs acceptance itself and fixes,
max 5 attempts. Judge it by outcome, not by its self-reported attempt count.

**L1 - Codex reviewer (free).** Build the reviewer's `PROMPT.txt` exactly the way
§4 builds the worker's: the full contents of `references/review-protocol.md`,
followed by one line naming what to review.

```
Review the current working tree against .delegate-runs/<task-id>/SPEC.md — that file holds the GOAL, the FILE WHITELIST and BASE_SHA. Pre-existing dirty state is listed in .delegate-runs/<task-id>/BASELINE.txt. Do not modify anything.
```

```bash
python3 "$SKILL_DIR/scripts/dispatch.py" \
  --task-dir .delegate-runs/<task-id>-review/ \
  --repo "$PWD" \
  --prompt-file .delegate-runs/<task-id>-review/PROMPT.txt \
  --sandbox read-only
```

Its **own** task dir, and no `--mcp`. Reusing the worker's dir overwrites the
worker's `FINAL.txt` and mixes two transcripts into one `RAW_OUTPUT.log`.

Without that instruction line the reviewer has no way to find the spec: its
contract says "read the SPEC.md you were pointed at" and nothing points at it. It
then reviews the code for internal consistency alone, never checks the whitelist
or the GOAL, and returns `approve`. A confident, evidence-free approval is the
most expensive thing this protocol can produce.

The reviewer gathers its own evidence: `git status --porcelain`, `git diff`, AND
every untracked file - new files never appear in a diff, so a diff-only review of
a file-creating task reviews nothing. Verdict comes back in that dir's FINAL.txt
as <=5 lines. On `request-changes`, relay the findings verbatim into a retry (with
the turn number bumped, §4). You are a courier here: do not interpret or
pre-judge. Max 2 rounds.

**L2 - runtime verification (the architect's own eyes).** A green acceptance
command proves the code compiles and its unit checks pass. It does not prove the
feature works. For anything observable - UI, gameplay, rendering, animation -
Claude verifies it in the running application through its own MCP access, and
says plainly what it saw. Work is not done because a test passed; it is done
because someone looked.

**L3 - architect audit.** `git diff --stat`, then hunks for files the reviewer
flagged or the spec marked risky. Never the full diff by default. Max 2
architect-driven retry rounds, then stop and ask the user.

**Spin detection.** Two consecutive rounds failing with the same error signature
is spinning, not iterating. Escalate immediately.

**Classify before re-delegating:**
- Spec wrong or incomplete -> architect's fault: fix SPEC.md, then retry
- Spec right, implementation wrong -> narrow correction
- Acceptance command itself wrong -> fix the command, not the code
- Genuinely hard -> split the task, or do it yourself

## 9. Closeout

There is no commit step. Delivery is a report to the user.

1. Report in chat: what changed, YOUR acceptance result, what you observed in the
   running app (§8 L2), anything the worker flagged uncertain, and any scope
   violation. Everything sits uncommitted for the user to inspect.
2. Delete the run directory. The chat report is the promotion target - distill
   first, delete second.
3. The lock goes with it.

That order assumes the chat report captures everything worth keeping, which holds
when the deliverable is code - the code stays in the tree. It does not hold when
the deliverable is a document that cites its own evidence by path. For those, see
`references/research-task.md` §3: promote the report into the repo and keep the
probe scripts its open questions depend on, *then* delete the rest.

Abandoned task: summarise what was attempted, note the partial changes are
uncommitted, delete the run dir.

## 10. Recovery

One path. A fresh dispatch with the same PROMPT.txt but this instruction - and
the turn number, for the same reason as §4:

```
Read .delegate-runs/<task-id>/SPEC.md. Assess the current working tree against it and complete what is missing. Task dir: .delegate-runs/<task-id>/ — this is turn <N>; write your changelog to .delegate-runs/<task-id>/turn-<N>.md
```

The conversation is disposable; SPEC.md and the tree are the state. A fresh worker
reading the real tree is more trustworthy than a resumed one trusting its memory.

## Reference files

- `references/spec-template.md` - mandatory SPEC.md fields
- `references/worker-contract.md` - worker standing contract (verbatim into PROMPT.txt)
- `references/review-protocol.md` - reviewer contract
- `references/research-task.md` - variant for tasks whose deliverable is a report
- `references/setup.md` - environment setup and troubleshooting
