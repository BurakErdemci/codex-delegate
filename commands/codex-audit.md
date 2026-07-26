---
description: Audit code with outside eyes - Codex red team finds, Claude verifies and fixes
---

Load the codex-audit skill and run it in **session-audit mode** on the work just
done, unless the user explicitly asked for a comprehensive refactor.

Scope comes from `git diff` and the commits since the last ledger entry, not from
your memory of the session. Classify the threat surface before picking lenses.
Every finding needs a runnable proof before you act on it.
