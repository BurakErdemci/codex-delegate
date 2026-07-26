# SPEC.md Template

Every field is MANDATORY. If a field cannot be filled truthfully, the task is not
delegation-ready - do not dispatch. Write the whole file in English.

```markdown
# TASK <task-id>
BASE_SHA: <the SHA the lane worktree is pinned to - verify with `git -C <lane> rev-parse HEAD`>

## GOAL
<One sentence. Measurable. If it needs three sentences, split the task.>

## FILE WHITELIST
<Every path the worker may create or modify. The lane worktree starts clean,
 so the ENTIRE `git status --porcelain -uall` output is the worker's footprint
 and is compared against this list - anything missing here is flagged as a
 scope violation, including the worker's own changelog.>
- .delegate-runs/<task-id>/turn-*.md        # changelog - required, always
- src/feature/...
- (new) src/feature/NewThing.ext

## DO-NOT-TOUCH
- Any test file not named in the whitelist above
- Any other run's .delegate-runs/* directory
- DB schema, migrations, auth, payment code, CI config
- <project-specific additions>

## MCP
<none | server names>
<Only what this task genuinely needs - the server you would have used yourself.
 State what the worker may do with it, and name any operation it must not
 perform. Outward-facing servers require the user's explicit per-task approval
 in chat before dispatch.>

Example:
- unityMCP - create and wire components in the prototype scene. Do NOT enter play
  mode, take screenshots, or save over other scenes; the architect verifies
  behaviour separately.

## NETWORK
<allowed | not-allowed>
<Default not-allowed. Only allow when the task genuinely needs to fetch
 packages, and say what for. This is a per-task decision, never a standing one.>

## TESTS
<What the worker's own unit checks must cover. The worker writes these; they are
 its safety net, not the acceptance bar. Name any pre-existing test file it must
 leave alone.>

## ACCEPTANCE
<ONE runnable, self-contained command using a project-local runner. The architect
 runs this exact command independently; the exit code is the verdict.>

## CONVENTIONS
<2-3 existing files the worker must read first and imitate. Style is shown, not
 described - the worker reads them in its own context at zero cost to Claude.>
- src/existing/GoodExample.ext

## FORBIDDEN
- No new dependencies (unless listed here explicitly: <none | list>)
- No refactoring outside the whitelist, no drive-by cleanups
- No file deletion unless the whitelist marks it (delete)
```

## Rationale (for the architect - not copied into SPEC.md)

- **Changelog path inside the whitelist**: the worker is told never to write
  outside the whitelist, so the changelog must be listed or the two rules
  contradict each other. A worker was observed skipping the changelog while still
  reporting its path.
- **BASE_SHA in the file**: recovery runs and reviewers can establish the baseline
  without asking Claude.
- **Acceptance is one command**: multi-step criteria drift; a single exit code
  cannot.

## Writing an ACCEPTANCE command when the project has no test runner

Plenty of real projects cannot run their test suite headlessly - a game engine
holds a lock on the project, a build needs a GUI, the toolchain is not installed.
Do not skip acceptance because of this, and do not settle for "the worker says it
looks right". Find the cheapest command that would actually break.

The usual answer is a compile or typecheck. Engines and IDEs almost always ship
their own compiler and runtime somewhere inside the installation; a small script
that drives it directly gives you a real pass/fail without opening the editor.
Write that script yourself - INTO THE LANE - and point ACCEPTANCE at it. Then
add it to the whitelist marked as yours:

```
- (architect) tools/acceptance.sh   # written by the architect, not the worker
```

Without the mark, your own file surfaces in the lane footprint as an
unattributable path and you will report it as the worker's scope violation.

**Then verify the check can both fail AND pass.** Both directions, once, before
you dispatch. It costs two minutes and it is the highest-yield step in the whole
protocol.

- **Can it fail?** Introduce a deliberate error, confirm non-zero exit, revert.
  Do this INSIDE THE LANE, never in the main tree: the lane is disposable and
  `git -C <lane> checkout -- <file>` restores it perfectly, while the main tree
  is dirty by design and has no rollback point - an injected error that survives
  a session drop there is found days later, while chasing an unrelated bug. A
  command that passes on everything is worse than none: it manufactures false
  confidence in the worker's self-loop and in your own audit.
- **Can it pass?** Build a minimal correct fixture, confirm exit zero, delete the
  fixture. This is the direction people skip and it is the more expensive
  failure. A check that can never pass burns all five self-loop attempts and
  returns `STATUS: failed` on work that was fine - and the changelog will blame
  the implementation, not your checker.

If you wrote the check yourself rather than pointing at a project runner, assume
it is wrong until both directions are demonstrated. Two real examples, from a
checker that read correctly on the page and still could never pass:

- `re.compile(r"^- (/\S+)")`, used to pull file paths out of a report. Without
  `re.MULTILINE` the `^` anchors to the start of the whole string, so it matched
  nothing at all and every run failed identically.
- The same pattern with `\S+` truncated each path at its first space. The
  repository lived in `~/Documents/The Coding`, so every extracted path became
  `/Users/me/Documents/The`. **Build the fixture with your real paths**, not with
  `/tmp/foo.txt`; spaces, non-ASCII characters and symlinks all occur in real
  project paths and none of them appear in a toy fixture.

Say in the spec what the command does NOT prove. A compile check does not prove
behaviour, and the worker must not treat green as done.
