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

Script paths below assume a plugin install. If this skill was installed as a
plain user skill instead, the scripts sit next to this file — use
`~/.claude/skills/codex-delegate/scripts/` in place of `${CLAUDE_PLUGIN_ROOT}/skills/codex-delegate/scripts/`.

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

Do NOT delegate:
- Architectural decisions, or anything needing design taste
- Vague bug hunts - if you cannot write the spec, you cannot delegate it, and
  that filter is a feature
- Trivial edits (under ~30 lines) - the overhead exceeds the win
- Auth, payments, DB schema, migrations - architect-only surface
- Anything whose file whitelist you cannot enumerate precisely

## 3. Preflight

```bash
ls .delegate-runs/*/IN_FLIGHT 2>/dev/null       # must be empty - the only hard gate
python3 "${CLAUDE_PLUGIN_ROOT}/skills/codex-delegate/scripts/doctor.py" --check   # worker home + login
git rev-parse HEAD                              # record as BASE_SHA
ls -dt .delegate-runs/*/ 2>/dev/null | head     # dirs older than ~3 days are
                                                # abandoned tasks -> ask, then clean
```

Run `--check` BEFORE writing the spec. It costs no model tokens, and a broken
worker login otherwise surfaces only after the spec is written and dispatched.

A dirty working tree is FINE and expected. Instead of demanding a clean tree,
snapshot the pre-existing dirty state as a baseline and let §6 isolate the
worker's footprint by diffing against it:

```bash
git status --porcelain > .delegate-runs/<task-id>/BASELINE.txt
```

Abort only on an existing IN_FLIGHT lock.

## 4. Create the task

```
.delegate-runs/<task-id>/     # task-id: YYYY-MM-DD-shortname
    SPEC.md                   # durable contract - references/spec-template.md
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
`Read .delegate-runs/<task-id>/SPEC.md and execute it. Task dir: .delegate-runs/<task-id>/`

Disk is the durable state. Never architect around a session id; always architect
around SPEC.md.

## 5. Choosing the worker's MCP servers

Ask yourself: *if I were doing this task myself, which MCP would I reach for?*
That is the one the worker needs. Name it in the spec's MCP field and pass it as
`--mcp <name>`. A Unity task gets the Unity server; a mail task gets the mail
server; a pure refactor gets none.

Two limits:

- **Grant nothing else.** Servers the task does not need stay off, every time.
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

```bash
touch .delegate-runs/<task-id>/IN_FLIGHT
python3 "${CLAUDE_PLUGIN_ROOT}/skills/codex-delegate/scripts/dispatch.py" \
  --task-dir .delegate-runs/<task-id>/ \
  --repo "$PWD" \
  --prompt-file .delegate-runs/<task-id>/PROMPT.txt \
  --mcp <server>            # repeat per granted server; omit when none
```

Run it in the background: the harness wakes you when it exits, so do not poll.

**The lock is held until closeout (§9), NOT until the first result.** While
IN_FLIGHT exists Claude must not write to the working tree, must not use the MCP
servers granted to the worker, and must not start a second delegation. Reading,
planning, reviewing and talking to the user are all fine. This survives context
compaction, which is the point.

`dispatch.py` writes the transcript to `RAW_OUTPUT.log` and the worker's final
report to `FINAL.txt`. Read `FINAL.txt`. The transcript exists for forensics.

## 7. Verify before trusting

The worker's report is a claim. The observed failure mode is a worker reporting a
changelog path it never wrote - so check the disk first, always, even when the
code looks right.

```bash
test -f .delegate-runs/<task-id>/turn-<N>.md   # missing -> UNTRUSTED, turn failed
git rev-parse HEAD                             # must equal BASE_SHA
git diff --cached --stat                       # must be empty
git status --porcelain                         # footprint check vs BASELINE.txt
```

**Footprint check.** Diff the current `git status --porcelain` against
`BASELINE.txt`. Every path that is new, or whose status changed, is the worker's
footprint and must appear in the spec's FILE WHITELIST. A file that was clean at
preflight and is now touched but is not whitelisted is a scope violation: stop
and report. Files already dirty at preflight are pre-existing noise, but flag any
that are not whitelisted - the worker could have piggybacked on them and you
cannot fully attribute them.

Then run the acceptance command YOURSELF. The exit code is the verdict, not the
worker's claim about it.

## 8. Review - cheap layers first

**L0 - worker self-loop (free).** The worker runs acceptance itself and fixes,
max 5 attempts. Judge it by outcome, not by its self-reported attempt count.

**L1 - Codex reviewer (free).** Dispatch a second, read-only run with
`references/review-protocol.md` as the contract and `--sandbox read-only`. It
gathers its own evidence: `git status --porcelain`, `git diff`, AND every
untracked file - new files never appear in a diff, so a diff-only review of a
file-creating task reviews nothing. Verdict comes back in FINAL.txt as <=5 lines.
On `request-changes`, relay the findings verbatim into a retry. You are a courier
here: do not interpret or pre-judge. Max 2 rounds.

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

Abandoned task: summarise what was attempted, note the partial changes are
uncommitted, delete the run dir.

## 10. Recovery

One path. A fresh dispatch with the same PROMPT.txt but this instruction:

`Read .delegate-runs/<task-id>/SPEC.md. Assess the current working tree against it
and complete what is missing.`

The conversation is disposable; SPEC.md and the tree are the state. A fresh worker
reading the real tree is more trustworthy than a resumed one trusting its memory.

## Reference files

- `references/spec-template.md` - mandatory SPEC.md fields
- `references/worker-contract.md` - worker standing contract (verbatim into PROMPT.txt)
- `references/review-protocol.md` - reviewer contract
- `references/setup.md` - environment setup and troubleshooting
