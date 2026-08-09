# Safety Check Hook (Grok Build)

**Use this before any potentially destructive or high-risk operations.**

This is the Grok-native evolution of the Claude `safety-check.sh`.

## When to Activate

- Before running destructive shell commands (`rm -rf`, `git reset --hard`, database drops, etc.)
- Before large refactors that touch `core/` vs `app/` boundaries
- Before bulk changes across multiple files or repos
- When the user asks to "be careful" or "review for safety"

## Grok-Native Safety Protocol

Instead of simple pattern matching (like Claude's hook), do the following:

1. **Destructive Command Detection**
   - If the user (or you) are about to run dangerous bash commands, pause and ask for explicit confirmation.
   - Especially watch for anything involving production data, Docker volumes, or git history rewriting.

2. **Architecture Safety (Cerid-specific)**
   - Before any code move between `core/` and `app/`, **strongly recommend running `grok-preserve`** first.
   - Enforce the import-linter rules mentally on every structural change.

3. **Preservation-First Mindset**
   - Any change that could affect the `core/` ↛ `app/` contract must be reviewed with the Preservation Guard persona (see `grok-preserve` command).

4. **Trading Agent Safety**
   - In `cerid-trading-agent`, be extremely cautious with anything that could trigger live orders. Always verify execution environment (paper vs live).

## Recommended Behavior

When a safety concern is detected:
- Clearly state the risk
- Suggest safer alternatives
- Offer to run `grok-preserve` or relevant tests first
- Only proceed after explicit user approval

This hook should make Grok Build **more conservative and architecturally sound** than a pure reactive shell hook.
