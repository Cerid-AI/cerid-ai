# Preservation Guard Hook (Grok)

Strongly recommended before any structural changes in cerid-ai* repos.

**Behavior:**
- Run `make preservation-check` (or the equivalent import-linter + architecture tests).
- If the user is about to move code between `core/` and `app/`, or touch DI-threaded agents, force a preservation review.
- Use the `review` subagent with the "Preservation Guard" persona when this hook is triggered.

This is the Grok equivalent of the Claude safety/typecheck hooks, focused on the unique cerid architecture rules.
