# Hygiene - the vibe-code lens

Machine-written code has a characteristic residue: it over-explains, it hedges
its types, it leaves scaffolding behind, and it grows a second way to do
something rather than finding the first. None of that is a bug. All of it is
cost, paid by whoever reads the code next.

**Counters before opinions.** A model asked "is this code good?" always produces
something and its answer cannot be falsified. A count of `any` can be wrong, and
being wrong is what makes it useful. So: run the project's own toolchain first,
ask a model only for what no counter can see, and never let a model's aesthetic
verdict become a finding on its own.

**No new dependencies to measure.** Every project already ships most of this in
its compiler and linter. If a check genuinely needs a tool that is not installed,
report the gap rather than adding a package - the decision is the user's.

## Layer 1 - counters (free, exact, ratcheted)

Each of these is a number. The rule is not "reach zero", it is **the number does
not go up**. A ratchet stops drift without demanding a rewrite.

| Check | How | Note |
|---|---|---|
| escape-hatch types | count `any`, `as any`, `@ts-ignore`, `# type: ignore`, `unknown` casts | `any` in a test fixture is different from `any` on a public boundary - count them separately |
| strictness | is the strictest setting the toolchain offers actually on | a disabled flag is worth more than a hundred findings |
| unused code | compiler's own unused-locals/params, unreferenced exports | exported-but-unused is the common residue |
| dead branches | unreachable code the compiler already knows about | free |
| file size | files over ~400 lines | a threshold, not a verdict - flag for judgment |
| function size / nesting | over ~50 lines or 4 levels deep | same |
| duplicate blocks | identical or near-identical runs of lines | text-level only; variations are layer 2 |
| test coverage on changed lines | project's coverage tool, diff-scoped | whole-repo coverage is a vanity number; changed-line coverage is actionable |
| commented-out code | comment lines that parse as code | always a finding, see below |
| stale markers | `TODO`/`FIXME` with no reference, older than ~90 days by git blame | age from `git blame`, not from reading |

Record every count in the ledger. A hygiene pass that cannot show a number moved
did not measurably do anything.

## Layer 2 - judgment (scoped, or it drowns)

These need a reader. Scope them or the report becomes unreadable: **the diff
only** in mode A, **a per-file budget** in mode B.

### Comments

The full rule is in SKILL.md §5 because it is the easiest thing here to get
backwards. Restated for the worker brief:

There is no target density. Zero is a finding; a comment per line is a finding;
the ratio is never the metric. The test is **necessity, in both directions**.

Earns its place - carries what the code cannot:

- **why this way** and not the obvious alternative
- a **measurement** ("~48s for 14 min of audio on this machine")
- an **incident** that actually happened ("a worker reported a path it never wrote")
- a **non-obvious constraint** ("this table is only read under workspace-write")
- a **trap warning** ("a misspelled key here is ignored without error")
- the **scope of a constant** ("calibrated to one specific mic - recalibrate")

Noise - restates what the code already says:

- `// increment the counter` over `i++`
- a docstring that repeats the signature and adds nothing
- section banners with no content
- `// end of function`
- narration of the happy path a reader can follow from the code

**Missing rationale is equally a finding.** A non-obvious decision with no
explanation costs the next reader more than a redundant comment does. When a
line looks arbitrary - a magic number, an unusual order, a defensive check
against nothing visible - the finding is "no rationale", not "add a comment".
Those are different: the fix is to state the why, or to remove the arbitrariness.

The distinction to hold on to: a comment that would survive a rewrite of the
code below it is carrying information. One that would have to be rewritten with
it was describing the code, not the reasoning.

### Structural residue

- **Pass-through wrappers.** A function whose whole body is a call to another
  with the same arguments.
- **Single-use abstractions.** An interface, factory or base class with exactly
  one implementation and no second one in sight. The cost is paid, the benefit
  is not.
- **Parallel implementations.** Two ways to do the same thing, one newer, both
  live. The second one is usually the residue of an incomplete migration - and
  the finding is "which one is authoritative", not "delete one".
- **Defensive checks against impossible states.** A null guard on a value that
  cannot be null. Either the type is lying or the check is noise; both are
  findings and they have different fixes.
- **Error handling that discards the error.** `catch {}`, a bare `except:
  pass`, a swallowed rejection. This is the residue that costs most later,
  because it converts a loud failure into a silent one - the exact pattern this
  whole project's discipline is built against.
- **Config that duplicates config.** The same constant in two files, or a value
  set in code and again in a config file. Two sources of truth always diverge.

### Tests that assert nothing

Coverage counts a line as covered when it ran, not when it was checked. Look for:

- a test that calls a function and asserts only that it did not throw
- snapshot tests updated in the same commit as the behaviour they pin
- a mock so complete that the test exercises the mock
- a test whose assertions would pass against an empty implementation

The check that settles it: **would this test fail against naive or pre-fix
code?** If not, it is coverage without protection. That question is answerable
by trying it, which makes it a proof rather than an opinion.

## What is NOT a hygiene finding

Say this in the brief or the report fills with it:

- naming preferences, formatting, import order - the formatter's job
- "this could be more idiomatic" with no cost named
- suggested rewrites of code that works and reads fine
- missing features
- anything that cannot point at a file and a line

## Reporting

Hygiene findings do not need a proof script - they need a **location and a cost**.
Format is the same as `finding-contract.md`, with:

```
class:      comment-restates | any-on-boundary | pass-through-wrapper | ...
where:      src/thing.ts:41
cost:       one line - who pays, and when
proof:      n/a (hygiene)  |  probes/x.sh  (when a counter or test demonstrates it)
severity:   low unless it hides a real failure
```

A hygiene finding with no stated cost is a preference. Drop it.
