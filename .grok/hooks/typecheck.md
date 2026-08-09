# Typecheck Hook (Grok Build)

Run after editing TypeScript or Python files.

**Actions:**
- For Python: Run `mypy` (preferably inside the project's Docker environment or venv)
- For TypeScript/TSX: Run `npx tsc --noEmit` or the project's typecheck script
- Report errors clearly with file + line
- Suggest fixes using the `implement` or `review` subagent when many errors appear

Grok-native equivalent of the Claude typecheck hooks.
