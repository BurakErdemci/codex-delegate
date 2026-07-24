# Setup and troubleshooting

## One-time setup

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/codex-delegate/scripts/doctor.py" --init
python3 "${CLAUDE_PLUGIN_ROOT}/skills/codex-delegate/scripts/doctor.py" --smoke
```

`--init` creates `~/.codex-worker` with a minimal config and links its login to
your main Codex home. `--smoke` runs one tiny real turn, which is the only way to
know the login and the protocol both work.

Requirements: `codex` on PATH (0.145 or newer if you want to grant MCP servers),
Python 3.11+, and a Codex login (`codex login`).

## Why the worker gets its own CODEX_HOME

Not secrecy - least privilege. A normal `~/.codex` accumulates MCP servers with
credentials for outside services. A worker whose entire job is editing a working
tree has no business being able to reach them, and a tool it does not have is a
tool it cannot misuse. The worker home starts with no MCP servers at all; you add
exactly what a task needs.

This is defence in depth, not a guarantee: the worker still has a shell. The §7
checks are what actually catch a violation.

## Handing over an MCP server

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/codex-delegate/scripts/doctor.py" --list-mcp   # see what Claude has
python3 "${CLAUDE_PLUGIN_ROOT}/skills/codex-delegate/scripts/doctor.py" --add-mcp unityMCP
```

`--list-mcp` marks a server `blocked` when it carries credentials, points at a
remote endpoint, or is itself a Codex server (which would let the worker spawn
workers recursively). Overriding needs `--force`, and the skill still requires
the user's per-task approval in chat for anything outward-facing.

Registering a server does not grant it. Grants are per dispatch, via `--mcp`.

## Login desync - the failure that looks like a broken token

`codex login` only writes `~/.codex`. If the worker home holds its own copy of
`auth.json`, logging into a different account leaves the worker presenting the
old one, and every call fails with "refresh token was revoked" - or, once the
file is replaced but the process has already started, "you have since logged out
or signed in to another account". The CLI works fine the whole time, which makes
this look like anything but an auth problem.

`--init` links the two files so they cannot diverge, and `--check` compares the
account ids and repairs them. Run `--check` in preflight, before writing a spec.

## Why the run directory is `.delegate-runs/`

Under Codex's `workspace-write` policy, `<workspace>/.codex`, `.git` and
`.agents` are force-mounted read-only whenever they exist, recursively,
regardless of config. A worker asked to write its changelog under `.codex/runs/`
gets `patch rejected: writing outside of the project` on every attempt, even
though the path is inside its own cwd. This was hit empirically - a worker
produced correct code but failed the changelog step on every retry. Do not reuse
`.codex/` or `.agents/` for the run directory.

Add `.delegate-runs/` to `.gitignore`.

## Known behaviours

- `codex exec` can never make MCP tool calls. It has no handler for the
  server->client approval request, so they die as "user cancelled MCP tool call".
  This is why `dispatch.py` speaks the app-server protocol instead. The only
  `exec` workaround is `--dangerously-bypass-approvals-and-sandbox`, which
  removes the sandbox the whole model depends on.
- The MCP approval reply schema changed in codex 0.145: that request takes
  `{"permissions": ..., "scope": "turn"}`, while command and file-change
  approvals still take `{"decision": ...}`. `dispatch.py` checks the version and
  fails loudly rather than guessing.
- `dispatch.py` declines escalation requests by design. When the worker asks to
  act outside its sandbox, granting it would quietly undo the isolation.
- Observed failure mode: a worker reported a changelog path it never wrote. The
  `test -f` check in §7 is what catches this; do not relax it.
- Never let a worker read instructions written for a different dispatch
  mechanism. A worker once received a contract whose header described an MCP
  dispatch call and concluded it was supposed to delegate the task onward.
