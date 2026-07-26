# Lens catalogue

One lens = one subagent = one findings file. Pick lenses from the threat-model
answers in SKILL.md §2; do not run the whole catalogue by reflex. Name the ones
you skipped in the report - an unmentioned lens reads as a clean lens.

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
| none of the above (purely local, single user, no untrusted input) | `capability`, `secrets`, plus hygiene only |

That last row matters. A single-user local tool with no network surface genuinely
has a small attack surface, and the honest audit is short. Running eight lenses
on it produces eight files of speculation and teaches the user to ignore reports.

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

## Writing the lens brief

The lane's brief is the subagent's entire contract - it cannot see the
conversation. Per lens, state: the lens name, what it looks for, the findings
file path, the finding format (`references/finding-contract.md` verbatim), the
threat-model summary, and where today's changes are.

And state the two things that are easy to leave implicit:

> Report a finding only with a runnable proof under `probes/`. If you cannot
> build one, report it with `confidence: unverified` and say exactly what would
> settle it - that is a valid result and is not penalised.

> Do not report style preferences, hypothetical refactors, or missing features.
> If you cannot name a file and line, it is not a finding.
