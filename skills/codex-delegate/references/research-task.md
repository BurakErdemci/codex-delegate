# Research tasks

A variant of the protocol for tasks whose deliverable is a **report**, not code.

Most of the main protocol still applies. But three parts of it protect nothing
when no product code is produced, and one part actively backfires. This file
states exactly what changes; anything not listed here is unchanged.

## When this variant applies

The task produces findings rather than product code: protocol reconnaissance,
dependency audits, "how does X actually behave on this machine", codebase
archaeology, comparing two tools' wire formats.

The test: **if the task succeeds, does the working tree contain new product
code?** If no, use this variant.

Do not use it to dodge the acceptance bar on an implementation task. A task that
writes code and also produces notes is an implementation task.

## 1. The git baseline becomes an mtime window

`§3` snapshots `git status --porcelain` and `§7` diffs against it to attribute
the worker's footprint. A research worker writes only inside its own run
directory, so that diff shows the same thing whether the worker behaved or not -
and the repository may not be under git at all.

Replace it. Record the dispatch time, then look for anything modified inside that
window outside the run directory:

```bash
# preflight, right before dispatch
date +%s > .delegate-runs/<task-id>/DISPATCHED_AT

# closeout
find . -type f -newermt "@$(cat .delegate-runs/<task-id>/DISPATCHED_AT)" \
     -not -path './.delegate-runs/*' -not -path './.git/*'
```

Empty output means the worker stayed inside its whitelist. Anything listed that
you did not write yourself is a scope violation - stop and report, exactly as in
`§7`. Extend the check to any configuration directory the task could plausibly
have touched (`~/.codex`, `~/.claude`), since those sit outside the repo entirely.

`BASE_SHA` in SPEC.md becomes `BASE_SHA: n/a - not under git` when there is no
repository. Record the sha when there is one; it costs nothing and a recovery run
still benefits.

The worker's prohibition on running git stands either way.

## 2. ACCEPTANCE checks structure and traceability

There is no compile step and no test suite. Do **not** skip acceptance - a
research report is exactly the deliverable where "the worker says it looks right"
is most tempting and least safe.

Write a checker that verifies the report is shaped right and sourced. Three rules
carry the weight:

- **Required sections, named exactly in the spec.** The checker asserts every
  heading exists. This stops a worker from quietly dropping the question it could
  not answer.
- **A confidence line in every section.**
  `CONFIDENCE: verified-empirically | partially-verified | unverified`.
  Forcing a label per section is what stops "I read the documentation" from
  blurring into "I measured it". Say in the spec that `unverified` is always
  available and never penalised, or the worker will guess to look competent.
- **An evidence index.** Every raw capture and probe script, listed with absolute
  paths. The checker asserts each path exists and is non-empty. This is the
  load-bearing rule: it turns "I measured it" into a file somebody can re-run.

Then say in the spec what the check does **not** prove:

> This check verifies structure and traceability only. It cannot verify that the
> findings are correct - the architect audits that separately by re-running the
> probe scripts. A green check does not mean the task was done well. Do not
> optimise for the checker.

Parsing the evidence index is where these checkers break. Use a separator that
cannot occur inside a path (two or more spaces, or a tab), and read the warning
about `\S+` and `re.MULTILINE` in `spec-template.md` before writing the regex.

## 3. Evidence survives closeout

`§9` says delete the run directory. For a research task that orphans the report's
own evidence index - every absolute path in it stops resolving.

Closeout becomes:

1. **Promote the report** into the repository (`docs/`), with a provenance header:
   which run produced it, which tool versions, what was verified live versus read
   from a schema or a document, and what stayed open.
2. **Keep the probe scripts** when the report's open questions say "run this to
   close it". Deleting them means whoever picks up those questions writes them
   again from scratch.
3. **Delete the large one-off captures** once the report has distilled them.

## 4. Decide network before dispatch, and say what to do if it is denied

Live observation usually needs the network. Under `--sandbox workspace-write` the
worker's shell commands may have no outbound access, and the failure mode is
quiet: the worker's own model calls keep working, so it looks healthy right up
until the subprocess it spawned dies.

Fill the NETWORK field with the decision *and* the fallback:

> Attempt one live run early to find out which way it is; record the exact error.
> If network is denied: do not retry, do not look for a bypass, do not use
> `--dangerously-bypass-approvals-and-sandbox`. Mark every item that needs a live
> turn `CONFIDENCE: unverified`, state in OPEN QUESTIONS exactly which
> observations are missing and what command would produce them, and complete
> everything else. That is a successful turn, not a failed one.

Without that last sentence a blocked worker either thrashes through its five
attempts or reaches for the sandbox bypass. With it, the block becomes a
documented gap - which is a real result.

## 5. Reframe the contract in PROMPT.txt

`worker-contract.md` addresses an implementation worker. Append one paragraph
after it:

> NOTE FOR THIS TASK: this is a RESEARCH task, not an implementation task. It
> produces no product code. Everything above still applies - the file whitelist,
> the changelog, the acceptance command, the six-line final message - but "what
> you changed" means "what you investigated and wrote down".

## 6. Point CONVENTIONS at the closest document you already trust

A research worker imitates precision far better than it follows adjectives.
Naming one existing document - "match the precision of `docs/x.md`; note how it
separates verified facts from open questions and never blurs the two" - produces
a better report than three paragraphs describing rigour.

If prior art exists, name it and say how to treat it: *"read it for what it
learned; verify its claims rather than trusting them"*. Stale prior art is
common, and a worker told to verify will catch it.

## Where research tasks run

Usually NOT in a lane. A research worker produces no product code, so a
worktree protects nothing; give it a scratch directory outside any repository
(and `doctor.py --trust` it), or dispatch `--sandbox read-only` against the
real tree when the task is "read this codebase". The mtime window above is the
footprint check either way.

## What does not change

- FILE WHITELIST and DO-NOT-TOUCH as absolute boundaries.
- The changelog (architect seeds the skeleton, worker fills it) and the
  six-line final message.
- **Verify before trusting.** Run the acceptance command yourself. The worker
  reporting `ACCEPTANCE: pass` is a claim, not a result.
- MCP grant rules, including per-task approval for outward-facing servers.
- Few large tasks beat many small ones.
- Read `FINAL.txt`, not the transcript. Context isolation is the entire point and
  it matters more here, not less - a research worker reads enormous amounts.
