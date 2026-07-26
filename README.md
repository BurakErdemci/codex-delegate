# codex-delegate

A Claude Code plugin with two halves, both built on the same idea: **Codex does
the labour, Claude keeps the judgement.**

- **`codex-delegate`** hands implementation work to **parallel Codex worker
  lanes**, each in its own git worktree, while Claude stays the architect.
- **`codex-audit`** turns that machinery around: a Codex **red team** attacks
  the code Claude wrote, and Claude verifies every finding before acting on it.

## codex-delegate - parallel worker lanes

Claude writes the spec, decides what "correct" means, and judges the result.
Each Codex worker gets a disposable worktree pinned to a known commit, works
there, and reports back in six lines. Claude verifies the lane's footprint
against the spec, reads the diff, and applies it to the main tree itself. The
worker's reasoning and file reads never enter Claude's context.

The point is not that Codex is smarter. It is that Claude's context is the
scarce resource, and delegation spends someone else's.

## What makes this different from just asking another model

- **Lanes, not a lock.** v1 serialized everything behind a global lock so two
  writers could not collide in one tree. v2 gives every worker its own
  worktree - nothing to collide with, so independent slices run in parallel
  and the architect keeps working in the main tree meanwhile.
- **The footprint is exact.** A lane starts clean at a pinned SHA, so `git
  status` inside it IS the worker's footprint - every path is attributed, new
  files included, no baseline diffing. Anything outside the spec's whitelist
  stops the lane.
- **Nothing is trusted.** The worker's report is a claim. Claude re-runs the
  acceptance command itself, inside the lane, and reads the diff before one
  line of it reaches the main tree. A read-only Codex reviewer passes over the
  lane first, and its own footprint must be empty.
- **The worker can use MCP.** Grants are per task, registered up front, and
  outward-facing servers require your explicit approval each time - that part
  did not loosen.

## Install

```
/plugin marketplace add BurakErdemci/codex-delegate
/plugin install codex-delegate
```

Then, once - resolve the script path first (`$CLAUDE_PLUGIN_ROOT` exists for
the plugin loader, not in your shell):

```bash
SKILL_DIR=$(find "$HOME/.claude/plugins" -maxdepth 7 -type f \
  -path '*/codex-delegate/scripts/doctor.py' -print -quit 2>/dev/null \
  | sed 's|/scripts/doctor.py||')
echo "${SKILL_DIR:?not found - is the plugin installed?}"

python3 "$SKILL_DIR/scripts/doctor.py" --init
python3 "$SKILL_DIR/scripts/doctor.py" --smoke
```

`--init` builds an isolated Codex home for the worker and links its login to
yours. `--smoke` runs one real turn to prove the login, the model and the
protocol work - the only check worth trusting.

In every repo you delegate in, once:

```bash
echo '.delegate-runs/' >> .gitignore
```

**Requirements**

- `codex` on PATH, **0.145 or newer** (`npm i -g @openai/codex`), and a login:
  `codex login`. The approval reply schemas changed in 0.145; both `--check`
  and `dispatch.py` enforce the floor rather than guessing.
- **Python 3.11+.** Both scripts need `tomllib`; stock macOS `/usr/bin/python3`
  is 3.9 and both scripts say so plainly instead of tracebacking.
- macOS or Linux. Windows is untested.

Verified on macOS with codex-cli 0.145.0.

## Use

Just ask for work. Claude routes by one test: *can a complete spec - goal,
file whitelist, acceptance command - be written right now?* If yes and the
work is grunt work once specified, it goes to a lane; if no, the gap itself is
the reason Claude keeps the task. You can say "don't delegate" at any time and
it sticks. `/codex-delegate` nudges Claude to consider delegation explicitly.

What still asks for your word every time: granting an outward-facing MCP
server, and enabling network access for a lane.

To audit: say so when a piece of work is done - "audit today's work" - or
`/codex-audit`. There is no automatic trigger, deliberately: an audit spends
real time and tokens. A comprehensive refactor has to be asked for by name.

To let the worker use one of your MCP servers:

```bash
python3 "$SKILL_DIR/scripts/doctor.py" --list-mcp
python3 "$SKILL_DIR/scripts/doctor.py" --add-mcp unityMCP
python3 "$SKILL_DIR/scripts/doctor.py" --remove-mcp unityMCP   # undo
```

`--list-mcp` blocks servers that carry credentials, point somewhere remote,
look like they reach the network, or are themselves coding-agent servers
(detected by command, not name). Registering is not granting; grants happen
per dispatch.

## codex-audit - outside eyes on your own code

Claude wrote the codebase, so Claude is the wrong auditor for it. Ask it to
audit the day's work and it briefs a Codex red team from `git diff` (not from
its memory of the session - a narrative brief audits your intentions, the diff
audits your code), classifies the threat surface, and fans lenses out to Codex
subagents inside a disposable worktree where the sandbox holds but nothing is
off-limits.

Then the part that makes it usable: **a finding without a runnable proof is a
hypothesis, not a vulnerability.** Every finding ships a `probes/*.sh` that is
red now and green once fixed. Claude runs each proof first - findings that do
not reproduce die for free, before any agent looks at them. Survivors get
re-anchored in the live tree, judged for reachability, and high-severity ones
get an agent whose job is to *refute* them. Claude fixes what is left, and the
proof flipping green is what says it is fixed.

It also carries a **hygiene lens** for the residue machine-written code leaves:
escape-hatch types, dead code, pass-through wrappers, error handlers that
swallow the error, tests that assert nothing. Counters run before opinions,
because `any` count can be wrong and "this feels cleaner" cannot. Comments are
audited by **necessity, in both directions** - there is no target density, so
noise is a finding and so is a non-obvious decision left unexplained.

A second mode does a comprehensive refactor, and only on an explicit request:
deterministic inventory, Claude edits in batches, the full gate green after
each. A refactor's promise is "behaviour unchanged" and the gate is the only
thing that can support it.

Findings accumulate in an append-only ledger, which buys the one honest form of
learning: a finding class recurring across three audits is a pattern worth a
rule, and the evidence is those three findings - not a model's claim that it
learned something. The ledger is also the scalar the skill is judged by. If
confirmed-over-total collapses, it is producing noise and should be narrowed.

## How it works

`scripts/dispatch.py` drives `codex app-server` over JSON-RPC rather than
calling `codex exec` - `exec` has no handler for the approval request Codex
raises on an MCP tool call, so every one dies as `user cancelled MCP tool
call`, and the only workaround disables the sandbox entirely.

Everything else is files on disk, inside the lane: the spec, the prompt, the
full transcript, the worker's final report, and a round ledger. Disk is the
durable state - a lane survives context compaction and session death, and a
fresh worker pointed at the same spec resumes the work by reading the real
tree, not a memory of it.

## What it will not do

- Let a worker run git, ever. Lanes are integrated by the architect applying
  the diff; the work product stays uncommitted in the main tree for you.
- Reach any remote host, code forge, or deployment target.
- Delegate architectural decisions, vague bug hunts, auth/payments/schema
  work, or anything whose file whitelist cannot be enumerated up front. If the
  spec cannot be written, the task cannot be delegated - that filter is
  deliberate.

## Layout

```
commands/                        /codex-delegate and /codex-audit
skills/codex-audit/
  SKILL.md                       the audit protocol: modes, pipeline, decisions
  references/lenses.md           threat model -> lens sets, and each lens's blind spots
  references/hygiene.md          vibe-code checks; counters and judgement separated
  references/finding-contract.md the findings format, verbatim for the worker brief
skills/codex-delegate/
  SKILL.md                       the protocol Claude follows
  references/spec-template.md    mandatory spec fields
  references/worker-contract.md  the worker's standing contract
  references/review-protocol.md  the reviewer's contract
  references/research-task.md    variant for tasks whose output is a report
  references/setup.md            setup and troubleshooting
  scripts/dispatch.py            app-server client; one worker turn
  scripts/doctor.py              setup, trust, MCP handover, preflight checks
```

## License

MIT
