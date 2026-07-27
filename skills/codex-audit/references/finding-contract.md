# Finding contract

This file goes into the worker brief verbatim. It is the format every subagent
writes and the format Claude verifies against.

## Where findings go

**Write to disk. Do not report findings in your final message.**

```
.delegate-runs/<task-id>/findings/<lens>.md      one file per lens
.delegate-runs/<task-id>/probes/<name>.sh        one runnable proof per finding
```

Your final message follows the worker contract exactly: **six lines, nothing
else.** Findings never appear in it. The changelog's `files:` section lists
every findings file you wrote - that list is the only manifest there is.

The reason is measured, not stylistic. A parent agent asked to collect three
subagents' results into its final message returned one of them, and when told
explicitly to wait for all three and emit seven lines it emitted nine
characters. Both times the turn reported success. A worker handed a second,
competing final-message format followed neither and wrote prose - prose that
contained an invented causal claim. The filesystem has neither failure mode:
a file either exists and is non-empty or it does not, and that is checkable.

**Every lens named in your brief must end with a non-empty findings file.** A
lens that found nothing writes the file with `findings: 0` and one line saying
what it looked for. A missing file is an incomplete turn, not a clean lens.

## The one hard rule

**A finding without a runnable proof is a hypothesis, not a vulnerability.**

`probes/<name>.sh` must be self-contained, take no arguments, exit non-zero on
the current code, and be something that would exit zero once the flaw is fixed.
Red now, green after. That is the whole test.

**And state what the proof covers.** A probe that exercises one call path can
flip green while the class in `class:` stays alive through every other path.
So answer it explicitly: does the proof verify the whole class, or a single
path to it? Single path -> `confidence: partially-verified`, and say in the
finding which paths or surfaces stayed out of the probe's reach. Measured why:
a fixed finding's probe went green while the same capability stayed reachable
through a second entry point the probe never touched.

If you cannot build such a proof, you have two honest options, and both are
acceptable results:

- report it with `confidence: unverified` and state exactly what would settle it
- do not report it

What is never acceptable is a confident finding with no proof. An architect
acting on a phantom wastes more than a missed finding costs, and a report where
most findings do not reproduce gets the whole audit ignored.

`confidence: unverified` is never penalised. Do not upgrade a confidence label
to look thorough - a clear "unverified, here is the command that would settle
it" is worth more than a guess dressed as a measurement.

## Finding format

```markdown
## <short title>

class:      <kebab-case type>            # e.g. missing-owner-check
where:      <path>:<line>
proof:      probes/<name>.sh | n/a (hygiene)
reachable:  <who can trigger this, through which entry point>
severity:   high | med | low
confidence: verified-empirically | partially-verified | unverified

**What:** one or two sentences. What is wrong, mechanically.

**How it fails:** concrete inputs or state -> the wrong outcome. Not "could be
exploited" - the actual sequence.

**Proof:** what probes/<name>.sh does and what it prints when it fails.
```

Field notes:

- **`class`** is typed and reused. The same flaw in two places gets the same
  class. This is what lets the ledger count recurrences across audits, and a
  free-text title cannot be counted.
- **`where`** must be a real line in the tree you are reading. Never invent a
  line number; if you are unsure, name the symbol instead.
- **`reachable`** is the field that separates a finding from trivia. "Any
  unauthenticated caller via POST /orders" is a finding. "A developer editing
  this file by hand" is not. If the only path to it is someone already having
  full access, say so - that lowers severity honestly rather than inflating it.
- **`severity`** is about consequence times reachability, not about how clever
  the flaw is.

## Hygiene findings

Same file format, with `proof: n/a (hygiene)` and one extra field:

```
cost:       one line - who pays for this, and when
```

A hygiene finding with no stated cost is a style preference. Drop it rather than
report it. "Harder to read" is not a cost; "the next reader cannot tell which of
these two implementations is authoritative" is.

## Out-of-scope findings

If you notice something real outside your lens, do not chase it and do not
drop it. Add it at the end of your findings file under a `## Out of scope`
heading - one short entry each: where, what, one line on why it looks real.
No probe required; it enters the next brief's scope instead of dying in your
context. Measured: two narrow-brief agents each surfaced one out-of-scope
finding spontaneously, and one of those was the most serious flaw of the run.

## Do not report

- style, formatting, naming, import order
- hypothetical refactors, or "this could be more idiomatic"
- missing features the brief did not ask about
- anything you cannot point at a file and line for
- anything you did not observe yourself, reported as if you did

## What happens to your findings

Claude runs every proof. Findings whose proof does not reproduce are dropped
without further review. The survivors are re-anchored in the live tree, judged
for reachability against the project's threat model, and high-severity ones get
an independent agent whose job is to refute them.

This is not distrust of you specifically - it is that a claim and a fact are
different things, and the person acting on the finding has to be the one who
established it. Write for that reader: give them the proof, not the conclusion.
