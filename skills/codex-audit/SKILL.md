---
name: codex-audit
description: Audit a codebase with outside eyes - a Codex red team hunts vulnerabilities and vibe-code residue in a disposable worktree, Claude verifies every finding against the live tree and fixes what survives. Use when the user asks to audit the day's work, or explicitly asks for a comprehensive refactor.
---

# codex-audit - outside eyes on code Claude wrote

Claude wrote this codebase, so Claude is the wrong auditor for it. This skill
buys a fresh, adversarial reading from a different model family, then puts every
claim it makes through a gate before acting on any of it.

The division:

| | Codex red team | Claude |
|---|---|---|
| Hunting | tears the project apart in a throwaway worktree | writes the brief, picks the lenses |
| Evidence | must ship a runnable proof per finding | runs that proof; a claim that will not reproduce is dead |
| Judgement | none - it reports candidates | decides what is real, reachable, worth fixing |
| Fixing | never | does the fix, proven by the proof flipping red -> green |

**Why Claude fixes and Codex does not:** finding needs fresh eyes, fixing needs
context. A security fix is judgment work, which `codex-delegate` §0 puts on the
architect-only surface. Claude has the codebase in its head; the red team does
not, and a fix from a model that has read one lens of the project is how a
patch closes one hole and opens another.

**Requires the `codex-delegate` plugin** for `dispatch.py` / `doctor.py` and the
lane model. This skill is the audit protocol on top of that machinery.

## 0. Two modes, and the difference is not cosmetic

| | **A. Session audit** | **B. Comprehensive refactor** |
|---|---|---|
| Trigger | user asks to audit the work just done | user asks **explicitly**; never inferred |
| Shape | a **hunt** - find what nobody thought of | a **ratchet** - stop the drift, don't go backwards |
| Scope | today's diff is the priority, whole project is the boundary | the whole codebase, in batches |
| Codex role | red-team hunter, full fan-out | inventory only; sometimes not needed at all |
| Who edits | Claude, after verification | Claude, always, in gated batches |
| Bar | a runnable proof per finding | the gate stays green and the counters move down |

Mode B is never automatic. A comprehensive refactor of a working codebase is the
highest-risk operation in this skill: it touches everything, it is judged by
taste, and "no behaviour change" is only as provable as the project's tests.
**Thin tests mean small batches or no refactor** - say that out loud rather than
discovering it after 40 files moved.

Both modes include the hygiene lens (§5). Security lenses belong to mode A.

## 1. Trigger and scope

There is no automatic trigger, deliberately: this audit spends real time and
tokens, so it is the user's call. What the skill does instead is make it hard to
forget - the ledger (§7) records the last audit date, and when the commits since
then pass a threshold, say so unprompted.

**Scope comes from the trace, not from your memory of the session.** Write the
brief from `git diff` and the commits since the last audit. Your narrative goes
in as a *hint*, never as the boundary:

```bash
LAST=$(python3 - <<'PY'
import json,pathlib
p = pathlib.Path(".delegate-runs/AUDIT/ledger.jsonl")
lines = [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
print(lines[-1]["base_sha"] if lines else "")
PY
)
git diff --stat ${LAST:+$LAST..}HEAD          # what actually changed
git status --porcelain -uall                   # plus what is still uncommitted
```

A red team told "we added login" looks at login. A red team given the diff finds
the thing you touched and forgot to mention. That is the whole point of outside
eyes and a narrative brief throws it away.

**The diff sets priority, not the boundary.** The dangerous class is not a bug
introduced today - it is an old latent flaw that today's change made
*reachable*. Confining the hunt to the diff misses exactly that class. Give the
worker the whole project and tell it where today's changes are.

## 2. Threat model first - the lens set is derived, not fixed

Before fanning out, classify the surface. This is step one because skipping it
turns the audit into ritual: a report of "SQL injection" in a function only the
user can call from their own terminal burns money and drowns the signal.

Answer these from the code, not from assumption:

- Does anything accept **untrusted input**? From where - network, files, args,
  another process?
- Does it **listen** on a port or expose an IPC surface?
- Does it hold **credentials**, tokens, or user data?
- Does it **execute** anything - shell, eval, dynamic import, a child CLI?
- What **privilege** does it run with, and can a lower-privileged caller reach a
  higher-privileged path?
- What crosses a **trust boundary** - and is the boundary written down anywhere?

`references/lenses.md` maps each answer to its lenses. Pick only what the
answers justify, and say in the report which lenses you did NOT run and why -
a silent omission reads as "clean".

Worked example, from a real project in this repo's history: a desktop cockpit
whose only genuine vulnerability was that child CLI processes ran with
`bypassPermissions` and a cwd that could sit inside the repo. That is a
**capability** flaw, invisible to any lens looking for injection or unvalidated
input. The threat-model step is what puts that lens on the list.

## 3. The hunter lane

One `codex-delegate` lane, with two deliberate differences from that skill's
review lane:

- **`--sandbox workspace-write`, not read-only.** A red team that can only read
  recognises patterns; one that can run the code, write probe scripts and fuzz
  produces evidence. Measured: subagents inherit this sandbox - a write to
  `$HOME` was denied at the OS layer while a write inside the lane succeeded, so
  the worker can take the project apart and still cannot reach outside it.
- **Nothing integrates.** The worktree is disposable. Only two things come out:
  the findings files and the probe scripts. So there is no FILE WHITELIST to
  police and no scope-violation check - the lane may do anything to itself.

Codex has real subagents (`spawn_agent`, `wait_agent`, `list_agents` - runtime
tools, absent from `--help`). Tell the worker to use them; a comprehensive audit
is exactly the "one task, too much reading" case they exist for.

### The rule that makes this work: subagents write to disk

**Never ask the parent to aggregate its subagents' findings into its final
message.** Measured across three probes: the fan-out happens reliably, the
aggregation does not. Three subagents spawned, one value returned. Told
explicitly to wait for all three and emit seven lines, it emitted nine
characters. Every time `rc=0`, `turn/completed`, `FINAL.txt` populated - three
good signals over a one-seventh delivery.

So the filesystem is the aggregation layer:

- Each subagent writes its own `.delegate-runs/<task>/findings/<lens>.md`.
- The parent's final message is a **manifest** - which files it created, nothing
  more.
- Claude reads the files. The parent's context never holds the findings, which
  also serves the reason for fanning out in the first place.
- **Acceptance is mechanical:** every lens named in the brief has a non-empty
  findings file. Missing or empty -> the turn is incomplete, retry that lens. A
  manifest listing a file that is not on disk is the same failure the delegate
  protocol already guards against, and the same check catches it.

This is `research-task.md`'s evidence-index rule applied to a fan-out. Nothing
new is invented; the mechanism already exists in the sibling skill.

## 4. The finding contract

**A finding without a runnable proof is a hypothesis, not a vulnerability.**

This one rule is what keeps the skill alive. Ten findings with eight phantoms
and nobody opens the eleventh report. The cure is not diligence, it is an
artifact requirement.

Every finding, in `findings/<lens>.md`:

```
class:      missing-owner-check          # typed, so the ledger can count it
where:      api/orders.ts:88
proof:      probes/authz-1.sh            # RED right now; GREEN once fixed
reachable:  who can trigger this, via which entry point
severity:   high | med | low
confidence: verified-empirically | partially-verified | unverified
```

`confidence: unverified` is always available and never penalised - say so in the
brief, or the worker guesses to look competent. An honest `unverified` with a
clear "here is what would settle it" is a real result.

**Claude's verification, in this order** - cheapest first, and most findings die
before an agent is involved:

1. **Run the proof.** Does not reproduce -> the finding is dead. Free,
   deterministic, no judgment. This is the bulk of the filtering.
2. **Re-anchor it.** Open `where` in the live tree. Lanes lag; the line may have
   moved, the code may already be fixed.
3. **Judge reachability** - this is where Opus subagents earn their cost. The
   question is not "does the proof run" but "is this reachable by someone who
   should not reach it, given §2's threat model". A proof that demonstrates
   something harmless is still noise.
4. **Refute on purpose.** For anything high severity, one agent whose job is to
   *kill* the finding, not confirm it. Cross-model matters here: Codex found it,
   so Codex refuting it shares the same blind spots. Claude's own agents are the
   independent second reading.

Findings that survive all four are real. Everything else is logged as rejected
**with a one-line reason**, so it does not come back next audit.

## 5. Hygiene - the vibe-code lens

Runs in both modes. Different bar from security: no proof script, but also no
judgment call left to a model where a counter will do.

**Counters first, LLM last.** A model asked "is this code good?" always finds
something to say and its output cannot be falsified. `any` count is a number
that can be wrong. Run the project's own toolchain before asking anyone's
opinion: strict typecheck, unused-locals, lint, coverage on changed lines.

`references/hygiene.md` holds the full check list. The one that needs stating
here, because it is the easiest to get backwards:

### Comments

**There is no target density.** Zero comments is a finding. Comments on every
line is a finding. The number itself is never the metric - AI-written code skews
heavily toward over-commenting, and a skill that optimises the ratio downward
becomes a comment-deleting machine, which is worse than the disease.

The test is **necessity**, and it runs in both directions:

- A comment earns its place when it carries what the code cannot: **why this
  way**, a measurement, an incident that actually happened, a non-obvious
  constraint, a trap warning. This repo's own convention - rule plus rationale -
  is the same standard.
- A comment that **restates the code** is noise. `// increment i` over `i++`.
- **Missing rationale is equally a finding.** A non-obvious decision with no
  explanation costs a future reader more than a redundant comment does. Flag the
  gap, do not only flag the excess.

Mechanically detectable, and always findings: commented-out code (dead code
hiding in a comment), section banners with no content, docstrings that only
repeat the function signature, `TODO`/`FIXME` with no reference and months of
git age.

Everything else about comments is judgment - so scope it: **the diff only** in
mode A, and a per-file budget in mode B. A whole-codebase comment review
produces a 200-item report nobody reads.

## 6. Mode B - comprehensive refactor

Explicit request only. Sequence:

1. **Inventory, deterministically.** Counters and the project's own tools
   produce exact numbers: `any` occurrences, unreferenced exports, duplicate
   blocks, files over a size, functions over a complexity. Free and exact - no
   model needed and no model trusted.
2. **Codex lanes only for what counters cannot see**: is this abstraction used
   once, is this wrapper pass-through, does this pattern repeat with variations
   a text diff misses, is this comment carrying anything.
3. **Claude executes, in batches, gate after each.** The full gate green before
   the next batch. A refactor's whole promise is "behaviour unchanged", and the
   gate is the only thing that can support that claim.
4. **Judge by the numbers moving and the gate holding**, not by "it looks
   cleaner". Record before/after counts in the ledger. A refactor that cannot
   show a number moved did not do anything measurable.

Stop conditions, stated up front: gate red after a batch -> revert that batch,
do not push forward. Two batches in a row where the counters move but the gate
wobbles -> stop and report; something in the design is resisting, and that is a
conversation, not a bigger hammer.

## 7. The ledger - and the only honest form of "learning"

`.delegate-runs/AUDIT/ledger.jsonl`, append-only, one line per audit and one per
confirmed finding. It carries `base_sha`, date, lenses run, lenses skipped,
findings confirmed, findings rejected with reasons, and for mode B the
before/after counts.

Two things it buys:

**Recurrence, which is the valid version of "learn from your mistakes."** A
finding class appearing in three separate audits is a pattern, and the evidence
is those three findings - not a model's declaration that it learned something.
At the threshold, draft a candidate rule for the project's `CLAUDE.md` and let
the user approve, edit or reject it. **Never auto-promote.**

The forbidden version, and why: letting the model write "I learned to validate
input" into a durable file. There is no external validation, so a wrong lesson
calcifies and re-enters context every session with nothing to demote it. This is
measured, not theoretical - an agent product that commits self-authored lessons
was benchmarked at 45.0% -> 71.9% downstream failure across the
commitment boundary (arXiv 2607.10526), and the best-known predecessor shipped
the same `[LEARNED:]` pattern and was archived. Statistics find the candidate, a
model drafts the wording, a human approves.

**And the scalar this skill is judged by.** Confirmed findings over total
findings, across audits. If that ratio collapses, the skill is producing noise
and should be narrowed or dropped - and the ledger is what makes that
answerable instead of a feeling. A tool that cannot tell you whether it is
working is the failure mode this whole project came out of.

## 8. Report

To the user, in chat: which lenses ran and which did not and why, each confirmed
finding with its proof and reachability, what was rejected and why, what was
fixed and the proof flipping to green, what is left open. Then the ledger lines.

Nothing is committed. Fixes sit uncommitted for the user, same as
`codex-delegate` §1.

## Reference files

- `references/lenses.md` - threat-model answers to lens sets, with what each lens actually looks for
- `references/hygiene.md` - the vibe-code check list, counters and judgment calls separated
- `references/finding-contract.md` - the findings file format, verbatim, for the worker brief
