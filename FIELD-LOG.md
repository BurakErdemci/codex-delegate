# Field log

Every rule in these skills was bought by something breaking. This file is the
receipt: what failed, the number that proved it, and where the rule lives now.

It exists because the skills make claims about reliability, and a claim without
its measurement is the exact thing this project refuses to accept from a Codex
worker. Holding itself to a lower bar than it holds the tool would be the
easiest kind of dishonesty.

**How to read it.** Newest first, one section per release. Entries are short on
purpose - the rule itself lives in the skill files, and this is the index into
them. The last section lists what is still broken, because a log that only
records victories is marketing.

---

## What keeps recurring

Four patterns account for most of the entries below. They are worth reading
even if you skip the rest.

**Prose does not bind a deliverable the worker gets no feedback on.** A rule
written as "hard deliverable, never optional" produced the deliverable in 1
lane out of 6. Re-stating it with more emphasis produced it in 1 of 4. What
finally worked was making the worker's own acceptance command fail while the
file was missing. The escalation is now a design rule: *prose < a slot to fill
< the worker's own feedback loop.*

**The verification step is where measurements get lost.** Not the code under
test - the thing doing the measuring. A probe piped into `tail` reported
`tail`'s exit code and three probes looked like they passed. Probes re-run from
the lane measured the lane's stale copy and reported "unchanged" 13 times out
of 13. A launcher that could not start any script exited `1`, which is also the
contract's code for "the fault reproduces", so every finding read as confirmed
while nothing had run. Each fix is now mechanical rather than remembered.

**Breadth breaks coordination before it breaks anything else.** A parent agent
given seven lenses at once dropped six of its seven subagents. The same work
split one-lens-per-lane delivered. Nothing about the model changed; only the
shape of the brief.

**First-hand use only exercises the happy path.** Every round where the author
tested their own fix and called it done was followed by an independent review
finding something the author's habits never touched - most sharply, a cleanup
command that destroyed data when run from inside the directory it was cleaning
up, which the author never hit because the author always stood somewhere else.

---

## v2.10.0

**The tier map had no fan-out term, so it priced the wrong dial.** v2.9.0
documented model and effort as two dials but stopped at two tiers and said
nothing about how many lanes of each may run at once - and lane count, not
model name, is what ends a usage window: six `gpt-5.6-sol` lanes at `high`
exhausts it outright. The map is now three rows keyed on lane difficulty, each
carrying its own cap: `gpt-5.6-luna --effort max` for basic writing and review
with no cost cap on the fan-out, `gpt-5.6-sol` for middling work at `high`
alone and `medium` once a second sol lane opens with three lanes at most, and
`gpt-6-astra --effort high` for the hardest single seam, one lane. The worker
config default dropped to the cheapest row, because an unflagged lane is an
unrecorded routing decision and the expensive default paid for it silently.
Pairings verified against `model/list` on codex-cli 0.153.4 rather than
assumed: astra and sol accept `low` through `ultra`, luna stops at `max`.
(Tiering set by Burak, 5 Sep 2026.)

Writing the tier into the config exposed why the config could never carry it:
`--effort` defaulted to `high` and dispatch.py injects the flag
unconditionally, so the config's level was unreachable and every unflagged
lane ran at `high` no matter what the file said - the known-broken list had
been carrying this as an open item. The flag now defaults to nothing and the
config's level stands when no lane overrides it.
→ `codex-delegate/SKILL.md` §2 and §5, `codex-audit/SKILL.md` §5,
  `scripts/doctor.py`, `scripts/dispatch.py`

---

## v2.9.1

**The platform where the auth files always diverge was the one place nothing
said so.** `--init` symlinks the worker's `auth.json` to the main home so the
two cannot drift, but Windows refuses the symlink without privileges, so
`--init` falls back to a copy and warns - once, at install time. `--check`
then reported "login matches main home" forever after, comparing account ids
at that instant and saying nothing about the copy underneath. Found by
inspection on a Windows install: two `auth.json` files, different inodes,
different contents, different `last_refresh` stamps. `--check` now reports
whether the worker's login is a link or a copy.
→ `scripts/doctor.py`

---

## v2.9.0

**The archive was copied and never checked, and the next line was
irreversible.** `close` copied a lane's run directory into the main repo and
then force-removed the worktree. A copy that silently did not happen looks
exactly like one that did: nine lanes were removed this way in one round and
every proof script and finding text in them is unrecoverable. Presence is not
the test either - a truncated destination passes it - so `close` now compares
every copy against its source by sha256, writes `MANIFEST.sha256`, and removes
nothing unless everything matched. The mechanism already existed a few hundred
lines up, where `open` verifies its own rollback; only the destructive path
lacked it.
→ `codex-delegate/SKILL.md` §9, `scripts/new-lane.py`

**The approval filter's rules lived only in the code, so briefs kept walking
into them.** Every argv token naming a location outside the lane is declined,
and three false positives cost repeated rounds: fixtures built with
`mkdtemp()` land in the system temp directory, a container path passed as its
own argument is indistinguishable from a host absolute path, and a leading
backslash makes a regex fragment look like a UNC path. Written into the brief
instead, a lane that had been starving ran 23 approvals and 0 declines.
→ `codex-delegate/SKILL.md` §5

**`rc=5` was being read as a failed round.** It means the turn completed with
some commands denied. Across four audit rounds every such round still carried
sound findings, so the order is: read `findings/`, then the decline lines, then
decide. Re-dispatching on the exit code alone pays twice for a round that
already delivered.
→ `codex-audit/SKILL.md` §3

**The refusal antidote was bound to every brief except the riskiest one.**
v2.8.0 added "ask for a measurement, not for an attack" and attached it to the
hunter lenses, leaving it off the verification brief - the one text whose
stated goal is to defeat a security fix. The breaking framing kept dying on the
provider's classifier; the measuring framing carried the round every time.
→ `codex-audit/references/lenses.md`

**Two dials were being used as one.** Model and reasoning effort are both
per-lane, and neither was documented, so every lane inherited the config
default. Tiering the two independently - the cheaper model at max effort by
default, the expensive one at medium only where a missed defect would live in
production unnoticed - held five lanes at 56% of a five-hour window where
running them all expensive would have exhausted it. The same run killed a
tempting inference: the cheap lanes produced three to four times the transcript
and still cost less, so transcript volume is not a proxy for spend. An
unsupported model/effort pairing fails the turn as `turn/failed` with the
reason only in `RAW_OUTPUT.log`.
→ `codex-delegate/SKILL.md` §5, `scripts/dispatch.py`

**The worker login is a separate identity and rots on its own schedule.** Four
lanes died in the first second with a revoked refresh token while `codex exec`
from the main home answered normally - a different home, a different token,
and a profile untouched for a month belonging to a different account than
expected. `--check` compared account ids but never the age; it now warns past
30 days.
→ `scripts/doctor.py`, `codex-delegate/references/setup.md`

**A lane's environment can often be linked instead of installed.** The
toolchain section jumped from "a worktree carries no environment" to "install
it or accept static reasoning". Symlinking the main tree's `.venv` into a lane
turned a lens that could only write `unverified` claims into one that ran a
real probe and returned `verified-empirically`. The other end of the range was
measured in the same round: a compiled toolchain could be neither linked nor
installed and returned 20 of 20 findings unverified.
→ `codex-audit/SKILL.md` §3

**The setup page taught an idiom SKILL.md had already replaced.** The
`SKILL_DIR` lookup was corrected in SKILL.md - `-maxdepth 10` because the
installed path is 8 levels deep, `sort -V | tail -1` because `-print -quit`
picked 2.4.0 out of a cache that also held 2.5.0 - while the page a new
install actually follows kept handing over the broken form.
→ `codex-delegate/references/setup.md`

---

## v2.8.0

**The fix was the last unaudited surface.** The flow was: red team finds,
Claude verifies and fixes, done. The fix - fresh code, written under pressure
to make a probe turn green - was the one thing no outside eye ever read. Work
that had passed every closing step produced new holes under an independent
re-audit. The audit loop now sends the fix diff back through the red team and
terminates on zero blockers, capped at three rounds.
→ `codex-audit/SKILL.md` §4

**"Until both sides agree it is clean" is not a termination criterion.** A
reviewer told to look returns something every round. Findings now get a written
verdict: *blocker* (reachable in the real flow, with a consequence), *guard*
(unlikely only because a human habit prevents it - so the habit becomes a
check), or *demoted* (with the assumption it rests on named out loud). Only
blockers extend the loop.
→ `codex-audit/SKILL.md` §4

**Lane setup was six manual steps with no judgment in any of them.** Six lanes
in one session meant six passes through the same list, each step silently
wrong-able: forget the changelog skeleton and the turn is rejected
mechanically; skip the contract copy and the worker never learns the format;
branch from a stale base and nothing says anything at all. It is one command
now, and it refuses to print a dispatch line until the spec exists - that being
the only step that needs a person.
→ `scripts/new-lane.py`

**There was no probe runner, and the verification mismeasured itself.** Exit
codes were read through a pipe that swallowed them. The runner now enforces
both signals the contract requires - the interpreter handshake once per run,
the `probe root:` marker once per probe - reads exit status directly, and
compares the root each probe echoed against the one it was told to measure.
Seven verdict classes are fixture-tested; the two that matter most are a probe
exiting `1` with no marker (which is *did not run*, not *fault confirmed*) and
a probe exiting `0` against the wrong tree (*wrong tree*, not *fixed*).
→ `scripts/run-probes.py`

**Probes encoded preconditions the intended fix contradicted.** Two went
invalid the moment the fix landed - one asserted a clean exit on the way in
while the fix was *to start failing loudly*; the other's success branch was
structurally unreachable. Findings now carry `green_when:`, read before the fix
is written rather than after it breaks.
→ `codex-audit/references/finding-contract.md`

**Detached lanes had no completion signal**, so every lane grew its own watcher
loop and no two agreed on what to watch for. `--done-file` is written however
the turn ends - success, failure, timeout, rejected argument, uncaught
exception, or a kill signal.
→ `scripts/dispatch.py`

**A provider refusal can leave nothing at all.** The earlier record held that
refusals arrive after the work, so disk artifacts survive; that is true only
sometimes. One refusal arrived on the first turn and left an empty directory.
Red-team briefs are structurally the highest-refusal-risk text this plugin
produces, and the skill now writes them as measurements - what to measure and
which comparison settles it - rather than as attacks. Same task, same scope,
the operator's own repository; only the shape of the request changed. This is
not, and must not become, a way to get through something a provider would be
right to refuse.
→ `codex-audit/references/lenses.md`

**Exit 5 contradicted the instruction that read it.** A wrapper change made
`dispatch.py` return 5 for "turn completed but permissions were declined",
while the skill still said non-zero meant the turn never completed and the
report was garbage. On exit 5 the report is real. Every exit code now has a row
saying whether a report exists.
→ `codex-delegate/SKILL.md` §5

### Found by v2.8.0's own verification round

The round that this release added, run against this release's diff: three
blockers and eleven guards in the author's own fix.

- **A cleanup command destroyed the thing it was archiving.** `close` resolved
  the repository from the current directory, so running it from inside the lane
  put the archive inside that lane and the next line deleted both. Spec,
  changelogs, findings and probes, with no copy anywhere. Standing inside the
  lane is the normal way to close it; the current directory is the one thing
  that must not decide this.
- **The probe runner silently dropped probes it did not recognise** and never
  descended into subdirectories, while still reporting that everything had been
  judged - the precise failure the file was written to prevent.
- **The completion marker was not written when an argument was rejected**, so a
  typo in a hand-edited dispatch line left the documented poll spinning forever.

A later round found the same class once more, in a different disguise: an
interpreter can answer the skills' handshake and still be unable to run
`dispatch.py` (it needs 3.11 for `tomllib`). The import failed before any
marker could be written. The version gate now runs before that import and
writes the marker itself.

---

## v2.7.x - the Windows rounds

Six releases came out of running the plugin on Windows, where no OS sandbox
backs the worker and the containment falls to a path-scoping check.

**Every command was declined for the wrong reason.** Codex wraps commands as
`powershell -Command ...`, and the containment check judged argv[0] - the
interpreter's own absolute path - as an out-of-lane write. Eight dispatches, 74
declines, zero approvals, zero artifacts. Worse than the blockage: a genuinely
escaping write was declined for that same wrong reason, so the real rule was
never exercised. argv[0] is the program, not an operand.

**The decline line recorded no reason**, so the root cause above could not be
read out of the transcript and had to be found by replaying captured payloads
through the function by hand. The reason rides in the log now.

**A file-write approval carries no path.** The paths travel one notification
earlier, in `item/started`; the approval itself names only an item id. Path
collection came back empty and the conservative decline fired on writes that
were entirely inside the lane. Items are cached by id and the approval is
enriched before the rule applies.

**The approval reply never reached the tool.** The wire verb for yes is
`accept`, not `approve`; the malformed reply was dropped by the router while
our own log recorded an approval. A turn logged three approvals and wrote
nothing to disk. ("Decline" had always been valid - which is why refusing
always worked and only approving silently failed.)

**A field report named the wrong version.** The report said the current release
was still blocking; the logs showed all nine turns had run the previous one, so
the patch under discussion had never been field-tested at all. Establish which
version actually ran before acting on a report about it.

**When writes are blocked, findings die with the turn** unless the brief says
to put them in the final message. Twenty-one findings survived one such run
only because that sentence had been added by hand; the same blockage the day
before, without it, produced nothing.

**`python3` on Windows is often a Store stub** that answers `command -v` and
runs nothing. The interpreter is resolved once, by handshake, and used
everywhere after.

**Preflight edited the operator's tree** to satisfy its own precondition,
inserting a fourth unreviewed change into a repository being kept deliberately
clean. It reports and stops now.

**Lanes carry no toolchain** - no virtualenv, no `node_modules`, no network -
so a lens whose probes need the project's dependencies cannot verify anything.
"Runs without dependencies" became a lens-selection criterion.

**A containment probe measured the harness, not the sandbox.** It reported
"contained" when what had actually happened was the dispatcher declining the
request. The decline line in the transcript is the evidence of which layer
answered.

---

## v2.6.0 - v2.7.0

**Probes measured the tree they were written in, not the tree that was fixed.**
Thirteen of thirteen verdicts were wrong, and changing directory did not help,
because a probe that derives its root from its own location follows the script.
The root is passed explicitly and echoed as the probe's first line.

**The first draft of that very fix was itself false-green:** with the target
file missing, the pattern reported "fixed". Retested under five conditions
before it was allowed to stand. A tool that produces evidence has to be tested
in both directions, exactly like the evidence it produces.

**A probe that greps source text is not a proof.** A fix changed the wording,
the flaw stayed, and the probe still reported it live. Probes exercise
behaviour; when behaviour genuinely cannot be exercised, the finding is
`unverified` rather than dressed up.

**The scope query read the wrong ledger line.** It took the last line, but the
ledger interleaves audit rows and finding rows, so the last line is almost
always a finding with no base commit - and the audit's scope silently became
"nothing". A query that fails to a blank instead of an error is the dangerous
shape.

**The map replaced the lanes.** The threat-modelling pass produced enough
findings that the fan-out was skipped, and the audit finished with no outside
eyes at all - which is the entire product. A gate closes that path, and the
report has to say which lanes ran.

**The disjointness rule was written for lanes only**, so two subagents were
handed the same file. Nothing was lost, by luck. Overlapping territory is the
architect's error; the fix is to split so there is nothing left to coordinate.

---

## v2.2.0 - v2.5.0

**A finding without a runnable proof is a hypothesis.** Three plausible claims
died the moment they were run as proofs; the one real finding of that run
appeared in no lens report and was caught by a two-way acceptance test. This is
the rule the whole audit skill rests on.

**Seven lenses in one brief delivered one.** Two single-task briefs delivered
both, and each volunteered a real finding outside its own scope. Model and
structure changed together, so this ranks no executor - what it establishes is
that the narrow brief is the delivering shape.

**Two contracts specified two different final-message formats.** The worker
followed neither, wrote prose both times, and one of those prose reports
invented a causal claim about who had deleted a set of fixtures. Prose is where
confabulation lives; one format, everywhere.

**An optional heading went unused three times out of three** while the same
workers volunteered exactly that kind of information elsewhere, unprompted. The
information was being produced; the heading was not being reached for. It is a
required section now, named for what workers already write.

**A class was declared closed on a single before/after count**, and the red
team promptly found two more forms of it. Closure requires naming and checking
at least two variants; a closure without that list is not a closure.

**`turn/completed` is not a success signal.** It also carries provider
refusals. One lane in four was refused, and the wrapper reported OK - an empty
lane delivered as a finished one.

**Probes went stale with no way to say so.** A refactor invalidated one and it
kept "passing". Three states now: the fault is live, the fault is gone, or the
probe can no longer measure what it was written for - and the third is a
finding, never a pass.

**Script discovery found nothing, then found the wrong version.** The search
depth was one level shallower than the real install path, so it returned empty;
fixing that exposed the second half, where the first match wins and the first
match is whichever version the filesystem hands over - a previous release's
scripts running under this release's protocol.

**The shell trap that keeps coming back.** zsh aborts an entire command when a
glob matches nothing, so cleanup steps silently did not run and fixture files
survived into the next lane. It was fixed in one skill and not carried into its
sibling, where it promptly happened again. Separately, zsh does not
word-split an unquoted variable, which made a copy step skip five files while
the verification still reported green - because it compared tracked files and
the missing ones were untracked. A verification that measures the wrong thing
is worse than none.

---

## Before v2.2.0

The first independent review of this repository - the first time anyone other
than the author read it - returned 39 findings, five of them blockers. The
author had used the tool end to end and found four rough edges. All five
blockers were on retry, error and second-round paths: the places a single
straight-through run never visits.

The classes, since they recur: a report file that was not written on the error
path, so the previous round's success was read as this round's result; a
timeout that could never fire because the read it guarded blocked forever;
diagnostics discarded at the one place startup failures are reported; a
protocol step that referenced a directory a later step created; and a
documented command that began with a variable which is empty in the shell the
user actually types into.

That review is why the verification round exists at all.

---

## Known limitations

Verified against the current tree. These are open, not forgotten.

- **Transcript truncation is silent.** Long items are cut at fixed limits with
  no marker saying anything was removed. The worst case is a truncated record
  of a declined command - the evidence of an isolation attempt.
- **The transcript never rotates.** It opens in append mode with no size cap,
  so repeated dispatches into one task directory grow it without bound.
- **The worker model is hardcoded**, with no flag and no schema stamp on the
  worker config, so a model change in a new release never reaches an existing
  install and a user without access to that model fails at every dispatch.
- **MCP registration reads one config layer.** A server defined in another
  layer is reported as unregistered.
- **Orphaned children on POSIX.** The process-tree kill exists on the Windows
  branch only; on POSIX a killed dispatch can leave grandchildren running.
- **A version string with a pre-release suffix fails to parse.**
- **`turn/completed` is only recognised in the main loop.** If it arrives while
  a request is awaiting its response it is merely logged, and the main loop
  waits for a second one that never comes.
- **A mistyped config override is silently ignored** by the tool it is passed
  to, so the setting appears applied and is not.
- **Nested agent invocations are a blind spot.** A worker granted network
  access could start a second agent with its own permissions, which the
  dispatcher's approval logic never sees.
- **The final report is the last agent message.** A worker that writes its
  report and then says "Done." ships "Done."

---

## Method

Entries here are written from traces, not from memory: exit codes, transcripts,
file contents, and counts. Where an entry says a number, that number was
measured. Where it says nothing was measured, no number is claimed.

The convention that keeps this file honest is the same one the skills apply to
a Codex worker: a claim and a fact are different things, and the person acting
on it has to be the one who established it.
