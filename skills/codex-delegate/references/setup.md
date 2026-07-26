# Setup and troubleshooting

## One-time setup

`${CLAUDE_PLUGIN_ROOT}` is set for the plugin loader and **not** in your shell,
so resolve the script path first - pasting that variable into a terminal gives
you an empty path and a misleading "No such file or directory":

```bash
SKILL_DIR=$(find "$HOME/.claude/plugins" -maxdepth 7 -type f \
  -path '*/codex-delegate/scripts/doctor.py' -print -quit 2>/dev/null \
  | sed 's|/scripts/doctor.py||')
echo "${SKILL_DIR:?codex-delegate scripts not found - is the plugin installed?}"

python3 "$SKILL_DIR/scripts/doctor.py" --init
python3 "$SKILL_DIR/scripts/doctor.py" --smoke
```

No wildcards on purpose: zsh, the macOS default shell, aborts the entire
command when a glob matches nothing. `find` has no such behaviour.

`--init` creates `~/.codex-worker` with a minimal config and links its login to
your main Codex home. `--smoke` runs one tiny real turn, which is the only way
to know the login, the configured model and the protocol all actually work.

Requirements:

- `codex` on PATH, **0.145+** (`npm i -g @openai/codex`). Both `--check` and
  `dispatch.py` enforce the floor: the approval reply schemas changed in 0.145.
- A Codex login: `codex login`, before `--init`. If your main Codex home is not
  `~/.codex`, set `CODEX_HOME` - doctor honours it.
- **Python 3.11+**, because both scripts import `tomllib`. Stock macOS
  `/usr/bin/python3` is 3.9; both scripts now exit with a plain message instead
  of a traceback, but you still need a newer interpreter
  (`brew install python`, then `python3.11`/`3.12`/`3.13`).
- POSIX shell (macOS/Linux). The preflight and lane commands are POSIX;
  Windows is untested.

## Why the worker gets its own CODEX_HOME

Not secrecy - least privilege. A normal `~/.codex` accumulates MCP servers with
credentials for outside services. A worker whose entire job is editing a
working tree has no business being able to reach them, and a tool it does not
have is a tool it cannot misuse. The worker home starts with no MCP servers at
all; you add exactly what a task needs.

This is defence in depth, not a guarantee: the worker still has a shell, and a
granted MCP server runs OUTSIDE the Codex sandbox entirely. The lane footprint
check (SKILL.md §6) is what actually catches a filesystem violation; MCP side
effects are governed by the contract and the per-task grant, nothing else.

## Folder trust - the failure that looks like a hung turn

Codex asks for folder trust **per exact project path**; a trusted parent does
not cover its children (measured: a field config carried `/private/tmp` AND
`/private/tmp/cdtest` as separate entries - the same failure patched by hand,
twice). dispatch.py can only answer a trust request emptily, so an untrusted
cwd stalls or fails the worker's first turn with no visible cause.

Every lane worktree is a NEW path, so every lane needs:

```bash
python3 "$SKILL_DIR/scripts/doctor.py" --trust "<lane path>"
```

SKILL.md §4 includes this step; `--smoke` trusts its own temp dir the same way.

## Handing over an MCP server

```bash
python3 "$SKILL_DIR/scripts/doctor.py" --list-mcp     # what Claude has, incl. plugin servers
python3 "$SKILL_DIR/scripts/doctor.py" --add-mcp unityMCP
python3 "$SKILL_DIR/scripts/doctor.py" --remove-mcp unityMCP   # undo
```

`--list-mcp` marks a server `blocked` when it carries credentials, points at a
remote endpoint, looks like it reaches the network, or is itself a coding-agent
server (detected by command, not name - a Codex bridge registered as `impl`
would otherwise let the worker spawn workers recursively). Overriding needs
`--force`, and the skill still requires the user's per-task approval in chat
for anything outward-facing.

Registering a server does not grant it. Grants are per dispatch, via `--mcp`.
But note what "granted" means: the server runs as a separate process outside
the sandbox, and its tool calls are auto-approved for the turn. The `local?`
label is a heuristic, not a safety verdict - check the server yourself.

## Login desync - the failure that looks like a broken token

`codex login` only writes the main home. If the worker home holds its own copy
of `auth.json`, logging into a different account leaves the worker presenting
the old one, and every call fails with "refresh token was revoked" - or, once
the file is replaced but the process has already started, "you have since
logged out or signed in to another account". The CLI works fine the whole time,
which makes this look like anything but an auth problem.

`--init` links the two files so they cannot diverge, and `--check` compares the
account ids. API-key auth is untested; `--check` skips the comparison for it
and says so.

## Why the run directory is `.delegate-runs/`

Under Codex's `workspace-write` policy, `<workspace>/.codex`, `.git` and
`.agents` are force-mounted read-only whenever they exist, recursively,
regardless of config. A worker asked to write its changelog under `.codex/runs/`
gets `patch rejected: writing outside of the project` on every attempt, even
though the path is inside its own cwd. This was hit empirically - a worker
produced correct code but failed the changelog step on every retry. Do not
reuse `.codex/` or `.agents/` for the run directory.

Add `.delegate-runs/` to the main repo's `.gitignore` (SKILL.md §3 checks).
Inside a lane it is excluded from the footprint command by pathspec, so the
gitignore state of BASE_SHA does not matter there.

## Lanes and dependencies

A fresh worktree contains tracked files only: no `node_modules/`, no venv, no
build output. If the spec's ACCEPTANCE command needs dependencies, install them
in the lane before dispatching - a worker that cannot run its acceptance
command burns its five attempts on an environment problem and reports `failed`
on work that might have been fine.

## Known behaviours

- `codex exec` can never make MCP tool calls. It has no handler for the
  server->client approval request, so they die as "user cancelled MCP tool
  call". This is why `dispatch.py` speaks the app-server protocol instead. The
  only `exec` workaround is `--dangerously-bypass-approvals-and-sandbox`, which
  removes the sandbox the whole model depends on.
- The MCP approval reply schema changed in codex 0.145: that request takes
  `{"permissions": ..., "scope": "turn"}`, while command and file-change
  approvals still take `{"decision": ...}`. `dispatch.py` checks the version
  and fails loudly rather than guessing.
- `dispatch.py` declines escalation requests by design. When the worker asks to
  act outside its sandbox, granting it would quietly undo the isolation.
- Observed failure mode: a worker reported a changelog path it never wrote. The
  `test -f` check in SKILL.md §6 is what catches this; do not relax it.
- Never let a worker read instructions written for a different dispatch
  mechanism. A worker once received a contract whose header described an MCP
  dispatch call and concluded it was supposed to delegate the task onward.
- `ERROR: codex app-server exited unexpectedly` means the server died at
  startup; its stderr is interleaved into RAW_OUTPUT.log (unprefixed lines) -
  read the tail for the cause (bad config, unknown model, missing auth).
