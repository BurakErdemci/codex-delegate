# Lens catalogue

One lens = one findings file, always. Whether lenses share one lane or each get
their own is SKILL.md §3's width rule - more than three lenses never share one
worker. Pick lenses from the threat-model answers in SKILL.md §2; do not run
the whole catalogue by reflex. Name the ones you skipped in the report - an
unmentioned lens reads as a clean lens.

Each lens below states **what it looks for** and **what it is blind to**, because
a lens set chosen without knowing the blind spots produces false confidence.

## Selecting from the threat model

| If the answer is | Add these lenses |
|---|---|
| accepts untrusted input | `input-trust`, `injection` |
| listens on a port / exposes IPC | `surface`, `authz`, `dos` |
| holds credentials or user data | `secrets`, `data-exposure` |
| executes shell / eval / child processes | `capability`, `injection` |
| has privilege levels or multi-tenancy | `authz` |
| pulls third-party packages | `supply-chain` |
| concurrency, async, shared state | `state` |
| a dependency can fail mid-operation (network, disk, subprocess) | `error-path` |
| long-lived process, accumulating state | `resource` |
| nontrivial config or environment surface | `config-startup` |
| headed to production, must be diagnosable at 3am | `observability` |
| none of the above (purely local, single user, no untrusted input) | `capability`, `secrets`, plus hygiene only |

That last row matters. A single-user local tool with no network surface genuinely
has a small attack surface, and the honest audit is short. Running eight lenses
on it produces eight files of speculation and teaches the user to ignore reports.
The fragility rows are the exception to "short": a hardening request (SKILL.md
§0) switches them on even when the attack surface is small - a local tool can
still corrupt its own state on a half-finished write.

## The lenses

### `capability` - what can this process actually do
The most under-run lens and the one that caught the only real flaw in this
repo's own history: a desktop app spawning child CLIs with permission checks
bypassed and a working directory that could sit inside the source tree.

Looks for: child processes and their privileges, cwd control, sandbox or
permission flags passed to anything, file writes outside an intended root,
symlink following, temp files at predictable paths, anything that grants a
component more authority than its job needs.

Blind to: logic bugs inside a correctly-bounded component.

### `authz` - who is allowed
Looks for: missing ownership checks, IDs taken from the request and trusted,
role checks on the client side only, a path where a lower-privileged caller
reaches a higher-privileged routine, defaults that fail open.

Blind to: whether the permission model itself is the right one - that is design,
not audit.

### `input-trust` - where does data come from
Looks for: parsing without bounds, unvalidated size or depth, type assertions
on external data, trusting a field's shape because the happy path always has
it, `JSON.parse` on anything a stranger controls, integer handling at limits.

Blind to: input that is validated correctly and then used wrongly.

### `injection` - data becoming code
Looks for: string-built shell commands, SQL, paths, HTML, or regexes; `eval`
and dynamic import; template expansion on external data; argument arrays that
lose their quoting; a filename or branch name reaching a shell.

Blind to: injection through a dependency's own interface.

### `secrets` - credential flow
Looks for: hardcoded keys and tokens, secrets in log output or error messages,
credentials copied into a second file, `.env` committed, keys passed as argv
(visible in the process table), tokens in URLs, secrets surviving into a
subprocess environment that does not need them.

Blind to: a correctly-handled secret that the *service* then leaks.

A note from this repo's own reading of a public skills library: a repository
advertised a secret-scanning export tool as a selling point, that tool was not
shipped, and a live-format API key sat committed two directories away. A control
you wrote is not a control that ran - check the disk, not the README.

### `data-exposure` - what leaves
Looks for: over-broad responses, internal fields serialised outward, verbose
errors carrying stack traces or queries to a caller, logs recording payloads,
debug endpoints, third-party calls carrying more than they need.

Blind to: exposure through side channels (timing, size).

### `surface` - what is reachable
Looks for: routes and IPC channels with no auth, endpoints that exist only for
development, CORS and origin handling, an IPC bridge exposing more than the one
call it was added for, ports bound to all interfaces rather than localhost.

Blind to: reachability that depends on deployment config not in the repo.

### `state` - concurrency and ordering
Looks for: shared mutable state without a guard, check-then-act races
(TOCTOU), assumptions that events arrive in order, fixed temp paths that two
concurrent runs collide on, cleanup that runs while work is still in flight,
retries that are not idempotent.

Blind to: races that only appear under real load.

Measured example from this repo's reading: a script in a published skills
library used a fixed `/tmp` filter file while its own documented workflow was
"fire N parallel jobs" - two concurrent runs corrupt each other, and the failure
looks like a bad output, not a crash.

### `dos` - unbounded work
Looks for: unbounded reads into memory, no size cap on uploads or file opens,
regex with catastrophic backtracking, unbounded recursion, retries without
backoff, a queue with no ceiling, log files with no rotation.

Blind to: resource exhaustion caused by a dependency.

### `supply-chain` - third-party trust
Looks for: dependencies pulled unpinned, install scripts, `curl | sh` in setup
paths, a lockfile that does not match the manifest, transitive additions nobody
reviewed, packages whose name is a near-miss of a popular one.

Blind to: a compromised version of a package that pins correctly.
**Needs network** to check advisories - that is a per-task user decision
(`codex-delegate` §0), so default to reading manifests only and mark advisory
checks `unverified` when network is denied.

## The fragility family - production readiness

Security lenses ask who can break the code; these ask where it breaks by
itself. Same finding contract, same probe rule - a probe here **induces** the
failure (kill the subprocess mid-write, deny the config file, cap the file
descriptors, fill the temp dir) and shows the wrong behaviour. If the failure
cannot be induced cheaply, `confidence: unverified` with the command that
would settle it - same as everywhere else.

### `error-path` - what failure leaves behind
Looks for: swallowed exceptions, catch blocks that log and continue into
undefined state, partial writes with no cleanup or rollback, error handling
that loses the original cause, a failure inside cleanup that masks the real
error, retries that re-run non-idempotent work.

Blind to: the happy path, by definition. It complements the functional tests,
never replaces them.

### `resource` - what accumulates under normal use
Looks for: unclosed handles and connections, caches and queues with no bound,
missing timeouts on outbound calls, subprocesses never reaped, temp files
never deleted, listeners registered and never removed. `dos` covers an
attacker forcing exhaustion; this lens covers the process exhausting itself
on a normal Tuesday.

Blind to: leaks that need days of uptime to matter - report the mechanism with
a short-horizon probe, not a wall-clock proof.

### `config-startup` - the environment assumption
Looks for: config read without defaults or validation, a wrong assumption that
fails at first use instead of at boot (expensive versus cheap), version or
schema drift between components, ports and paths assumed free, a required
secret whose absence goes undetected until a request needs it.

Blind to: deployment topology that is not represented in the repo.

### `observability` - can a 3am failure be diagnosed
Judgment lens on the hygiene bar: `proof: n/a (hygiene)`, `cost:` required.
Looks for: errors logged without the cause or input that produced them, silent
fallbacks that mask degradation, failures that cannot be traced back to a
request, logging absent exactly on the paths the §2 map calls critical.

Blind to: whether anyone reads the logs.

## Writing the lens brief

The lane's brief is the subagent's entire contract - it cannot see the
conversation. Per lens, state: the lens name, what it looks for, the findings
file path, the finding format (`references/finding-contract.md` verbatim), the
threat-model summary, and where today's changes are.

**And state per lens whether its probes need the project toolchain.** A lane
carries no `venv`, no `node_modules` and no network, so nothing missing can be
installed - a lens whose probes cannot execute returns `unverified` findings at
full lane price (SKILL.md §3, measured 30 Jul 2026). "Runs without
dependencies" is therefore a lens-selection criterion, not a nice-to-have.

And state the two things that are easy to leave implicit:

> Report a finding only with a runnable proof under `probes/`. If you cannot
> build one, report it with `confidence: unverified` and say exactly what would
> settle it - that is a valid result and is not penalised.

> Do not report style preferences, hypothetical refactors, or missing features.
> If you cannot name a file and line, it is not a finding.

> End your findings file with a `## Coverage` section: surfaces examined (a
> table is fine), what you did not reach and why, and anything real you noticed
> outside this lens under `out of scope`. This section is required and is
> checked mechanically.

> Probes exit `1` when the flaw reproduces, `0` when it does not, and `2` when
> the probe itself no longer applies - print one line to stderr saying what
> stopped applying before exiting `2`.

> Write probes in the language named in this brief - `.sh` and `.py` are both
> first-class and the extension only picks the runner. Every rule above applies
> unchanged either way.

> A probe must exercise behaviour - call the function, start the process, send
> the request. Grepping the source for a pattern is not a proof: it tracks the
> wording of the code, and a fix that changes the wording flips it without
> changing anything real.

> Resolve the tree root as `${AUDIT_ROOT:-$(git rev-parse --show-toplevel)}`
> and echo it to stderr as your first line. Never derive it from the script's
> own path: probes are re-run later against a different tree.

## The verification brief - the fix diff as the target

Used by SKILL.md §4's verification round, after Claude has fixed the confirmed
findings. Same contract as every lens brief above; what changes is the target
and the framing. Give the worker:

- **the fix diff itself** (`git diff` of the fix commits or working tree), not
  the whole codebase - the round is cheap because the scope is small
- **the closure claims**: each closed class, the variants that were checked,
  and what the fix was supposed to buy
- three hunting directions, in priority order:

> 1. **Break the fixes.** Each closed class comes with the claim "this diff
>    closes it". Treat the claim as the target: find an input, path, or
>    encoding through which the class still fires despite the fix.
> 2. **Hunt what the diff introduced.** A fix is new code written under
>    pressure to make a probe go green. Look for what it broke, bypassed, or
>    newly exposed - especially in the neighbouring code it touched.
> 3. **Check the seams.** Where the diff meets unchanged code: changed
>    assumptions, changed error behaviour, changed types or defaults that the
>    unchanged callers still rely on.

The framing matters because this worker reads less code than a hunter lane
and must not pad the gap with speculation: findings still need runnable
probes, `confidence: unverified` is still a valid result, and a fix that
holds is a real answer - say so in the coverage section rather than
inventing something to report.
