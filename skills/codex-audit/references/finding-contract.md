# Finding contract

This file goes into the worker brief verbatim. It is the format every subagent
writes and the format Claude verifies against.

## Where findings go

**Write to disk. Do not report findings in your final message.**

```
.delegate-runs/<task-id>/findings/<lens>.md      one file per lens
.delegate-runs/<task-id>/probes/<name>.<ext>     one runnable proof per finding
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

**Write each finding to its file the moment it exists - never batch them for
the end of the turn.** The end of the turn is for the summary line, not for the
first write. "Files survive an aggregation failure" understates the reason:
measured 30 Jul 2026, a provider refusal killed a turn mid-run, *before* the
worker's first write, and three finished findings died with it - they were
recovered by hand from the raw transcript afterwards. A file that does not
exist yet survives nothing. So the loop is: finding found -> finding appended
to its lens file -> next surface. Only the `## Coverage` section and the
changelog's `files:` list are allowed to wait for the end.

**Every lens named in your brief must end with a non-empty findings file.** A
lens that found nothing writes the file with `findings: 0` and one line saying
what it looked for. A missing file is an incomplete turn, not a clean lens.

**One exception, and it is the disk itself: if your write commands are being
declined by the harness, the no-findings-in-final rule inverts.** Put every
finding - full format below, not a summary - into your final message, and say
there that you did so because writes were blocked. Measured 31 Jul 2026: eight
consecutive turns had every command declined (74 declines, 0 approvals), zero
findings files existed on disk, and 21 real findings - all 21 confirmed on
re-measurement, two of them product-breaking - survived only because the brief
carried this instruction. Without it, all eight turns end with nothing, which
is exactly what happened the day before it was added. A blocked write is the
one case where the filesystem has the worse failure mode; the six-line final
contract yields to salvage.

## The one hard rule

**A finding without a runnable proof is a hypothesis, not a vulnerability.**

`probes/<name>.<ext>` must be self-contained, take no arguments, exit non-zero
on the current code, and be something that would exit zero once the flaw is
fixed. Red now, green after. That is the whole test.

**That contract is binding; the language is not.** Write the probe in whatever
this machine can actually run - `.sh` and `.py` are both first-class, and the
extension only picks the runner (a verified bash, or the verified interpreter).
Every rule in this file applies identically to both. Measured 30 Jul 2026: the
first `bash` on the field machine was a WSL launcher stub with no distribution
installed while the code under audit was Python, so a `.sh`-only convention
would have ended the run with zero evidence. If the brief names a language for
this project, use that one.

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

Same three moves in the same order in a `.py` probe - resolve, print, guard:

```python
root = os.environ.get("AUDIT_ROOT") or subprocess.run(
    ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
print(f"probe root: {root}", file=sys.stderr, flush=True)   # first line of output, always
if not root or not os.path.exists(os.path.join(root, "<target>")):
    print(f"probe invalid: cannot locate target under root '{root}'", file=sys.stderr)
    sys.exit(2)
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

**There is a fourth outcome, and it is not an exit code: the probe never ran.**
An rc can only be produced by something, and that something is not always the
probe. Measured (Windows 11): `bash` first on `PATH` was the WSL launcher with
no distribution installed - `bash foo.sh` printed
`<3>WSL (9 - Relay) ERROR: CreateProcessCommon:800: execvpe(/bin/bash) failed`
and exited `1`, which this table reads as *the flaw reproduces*. The rc table
stays as written; what makes it trustworthy is the `probe root:` line above.
That line is printed by the probe body, so **output without it means the probe
never executed** - launcher failure, unreadable file, wrong interpreter - and
the verdict is "did not run", never `1`, `0` or `2`. Print it first, always,
before any other work: a marker emitted late cannot distinguish "never started"
from "died halfway". SKILL.md §4 carries the run-side rule (verify the shell
before any rc counts as a verdict).

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
proof:      probes/<name>.<ext> | n/a (hygiene)
reachable:  <who can trigger this, through which entry point>
severity:   high | med | low
confidence: verified-empirically | partially-verified | unverified

**What:** one or two sentences. What is wrong, mechanically.

**How it fails:** concrete inputs or state -> the wrong outcome. Not "could be
exploited" - the actual sequence.

**Proof:** what probes/<name>.<ext> does and what it prints when it fails.
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
