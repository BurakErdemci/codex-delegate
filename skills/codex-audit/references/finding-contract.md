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

**Measure behaviour, not source text.** Call the function, start the process,
send the request, import the module and assert what it does. A probe that
greps the source for a pattern tests the wording of the code, not the code:
measured, a worker wrote a regex-over-source probe, the fix changed the
wording without changing the flaw's status, and the probe reported the finding
still live. The `rc=2` escape does not catch this - the probe does not know it
has stopped measuring anything. If behaviour genuinely cannot be exercised
(the code path needs hardware, a service, a GUI), that is a
`confidence: unverified` finding, not a grep dressed as a proof.

**Resolve the tree root explicitly, never from the script's own location -
and verify it before measuring anything:**

```bash
ROOT="${AUDIT_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}"
echo "probe root: $ROOT" >&2                    # first line of output, always
[ -n "$ROOT" ] && [ -e "$ROOT/<the file or entry point this probe needs>" ] || {
  echo "probe invalid: cannot locate target under root '$ROOT'" >&2; exit 2; }
```

That guard is not boilerplate. Written without it - the first draft of this
very contract - a probe whose target is missing runs
`grep -q PATTERN "$ROOT/missing.py"`, grep fails, and the `|| exit 0` branch
reports **fixed**. Verified in a scratch repo: the unguarded form returned
`rc=0` for both a stale root and an empty root, which is the exact false-green
this section exists to prevent. A probe that cannot find what it measures
exits `2`, never `0`.

Never `cd "$(dirname "$0")/../.."`. Probes are written inside a disposable
lane and re-run later against the fixed main tree; a root derived from the
script's own path silently follows the script instead of the code under test.
Measured, and expensively: **13 of 13 probes were re-run against the stale
lane copy** and every verdict was wrong. Printing the resolved root as the
first line is what makes that visible in one glance instead of never.

**Three exit codes, not two.** A probe is a measuring instrument and it must be
able to say that it broke:

| exit | meaning |
|---|---|
| `1` | the flaw reproduces - the finding is live |
| `0` | the flaw does not reproduce - fixed, or never real |
| `2` | **probe invalid** - it can no longer test what it was written to test |

Print one line to stderr before exiting `2`, saying what stopped applying
(symbol renamed, file moved, entry point gone). A probe that silently keeps
passing after a refactor moved its target is a green light with nothing behind
it, and `2` is what prevents that. Measured: a refactor pushed a probe to
`rc=2` and the three-state design is what kept it from reading as fixed.

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

## `## Coverage` - a required section, not an optional one

Every findings file ends with a `## Coverage` section. It is checked
mechanically like the rest of the file: missing section = incomplete turn.

```markdown
## Coverage

surfaces examined: <list, or a table - whatever shape fits>
not examined:      <what you did not reach, and why> | none
out of scope:      <real things you noticed outside this lens> | none
```

**`out of scope` is where a real thing you noticed outside your lens goes.**
Do not chase it, do not drop it: where, what, one line on why it looks real.
No probe required - it enters the next brief's scope instead of dying in your
context.

Two measurements shaped this section. Narrow-brief agents surfaced serious
out-of-scope findings spontaneously - one was the worst flaw of its run. But
when the same thing was offered as a standalone optional heading it was used
**0 times out of 3**, while the same workers wrote coverage tables and review
sections of their own accord. The information was being produced; the heading
was not being reached for. So the section is now required, it is named for
what workers already write, and the out-of-scope entry lives inside it.

A table is a fine shape for `surfaces examined`. Do not flatten one into
prose to satisfy the format.

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
