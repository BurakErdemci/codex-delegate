# codex-delegate

A Claude Code plugin that lets Claude hand implementation work to a Codex worker
while staying the architect and the reviewer.

Claude writes the spec, decides what "correct" means, and judges the result.
Codex writes the code, runs its own checks, and reports back in six lines. The
worker's reasoning and file reads never enter Claude's context — only a short
structured report and the diff hunks review actually needs.

The point is not that Codex is smarter. It is that Claude's context is the
scarce resource, and delegation spends someone else's.

## What makes this different from just asking another model

Three things, and they are the whole design:

- **The worker can use MCP.** Not just the filesystem — the Unity server, a
  browser server, whatever the task genuinely needs. Grants are per task and
  outward-facing servers require your explicit approval each time.
- **Nothing is trusted.** The worker's report is a claim. Claude checks the disk,
  re-runs the acceptance command itself, and compares the entire working tree
  against the spec's file whitelist before believing anything.
- **A passing test is not "done".** Anything observable gets verified by Claude
  in the running application, through its own MCP access. Work is done because
  someone looked, not because a command exited zero.

## Install

```
/plugin marketplace add BurakErdemci/codex-delegate
/plugin install codex-delegate
```

Then, once:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/codex-delegate/scripts/doctor.py" --init
python3 "${CLAUDE_PLUGIN_ROOT}/skills/codex-delegate/scripts/doctor.py" --smoke
```

`--init` builds an isolated Codex home for the worker and links its login to
yours. `--smoke` runs one real turn to prove the login and protocol work — the
only check worth trusting.

**Requirements:** `codex` on PATH (0.145+ to grant MCP servers), Python 3.11+,
and a Codex login. Verified on macOS with codex-cli 0.145.0.

## Use

Invoke `/codex-delegate` in a session. Claude will not delegate without your
approval, and the approval does not carry into the next session.

To let the worker use one of your MCP servers:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/codex-delegate/scripts/doctor.py" --list-mcp
python3 "${CLAUDE_PLUGIN_ROOT}/skills/codex-delegate/scripts/doctor.py" --add-mcp unityMCP
```

`--list-mcp` blocks servers that carry credentials, point somewhere remote, or
are themselves a Codex server — that last one would let the worker spawn workers
of its own. Registering a server does not grant it; grants happen per dispatch.

## How it works

`scripts/dispatch.py` drives `codex app-server` over JSON-RPC rather than calling
`codex exec`. That is not a stylistic choice: `codex exec` has no handler for the
approval request Codex raises on an MCP tool call, so every one of them dies as
`user cancelled MCP tool call`. The only `exec` workaround disables the sandbox
entirely, which would defeat the isolation the whole design rests on.

Everything else is files on disk. Each run gets a directory holding the spec, the
prompt, a lock, the full transcript, and the worker's final report. Claude reads
the report; the transcript is there for forensics. Disk is the durable state, so
a run survives context compaction and can be recovered by pointing a fresh worker
at the same spec.

## What it will not do

- Touch git beyond reading it. Work stays uncommitted for you to review; there is
  no rollback point between rounds unless you make one.
- Reach any remote host, code forge, or deployment target.
- Delegate architectural decisions, vague bug hunts, or anything whose file
  whitelist cannot be enumerated up front. If the spec cannot be written, the
  task cannot be delegated — that filter is deliberate.

## Layout

```
skills/codex-delegate/SKILL.md   the protocol Claude follows
  references/spec-template.md    mandatory spec fields
  references/worker-contract.md  the worker's standing contract
  references/review-protocol.md  the reviewer's contract
  references/setup.md            setup and troubleshooting
scripts/dispatch.py              app-server client; one worker turn
scripts/doctor.py                setup, MCP handover, preflight checks
```

## License

MIT
