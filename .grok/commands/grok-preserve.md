# Preservation Guard (Grok Build)

Run strict architecture and preservation validation for the Cerid AI monorepo.

**Primary command:**
```bash
make preservation-check
```

**Additional checks this command should trigger:**
- Import-linter enforcement (`core/` must never import `app/`)
- Verify DI-threaded agents live in `core/agents/`, not `app/agents/`
- Check for violations of the established layering (routers are billing-only)
- Run any architecture tests defined in the Makefile or CI

**When to use:**
- Before any refactoring that touches `core/` vs `app/` boundaries
- After moving code between layers
- As part of `grok-across-cerid` when doing broad changes
- Before merging large PRs into cerid-ai-internal

This is one of the highest-value Grok commands because the cerid-ai architecture invariants are sacred.

If violations are found, use the `review` subagent with Preservation Guard persona and propose fixes.
