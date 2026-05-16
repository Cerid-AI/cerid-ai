# Python Lint Hook (Grok Build)

Run on Python file changes.

**Recommended checks:**
- `ruff check`
- `ruff format --check`
- Project-specific linting rules from `pyproject.toml` or `Makefile`

Report violations and offer to auto-fix where safe (`ruff check --fix`).

This is the Grok version of the `pythonlint.sh` hook used in the cerid-ai family.
