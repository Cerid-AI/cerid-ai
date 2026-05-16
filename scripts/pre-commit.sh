#!/usr/bin/env bash
# Opt-in pre-commit hook. Wire it up with:
#
#   ln -s ../../scripts/pre-commit.sh .git/hooks/pre-commit
#
# (or `cp` if symlinks are problematic on your platform). Runs lightning-
# fast lint checks against staged files so common drift surfaces locally
# before it lands in CI.
#
# Catches:
#   - .env.example out of sync with config/settings.py
#     (CI gate: env-example-drift)
#   - MCP tool descriptions failing the "Use when / Returns" style
#     (CI gate: mcp-tool-descriptions — blocking as of v0.95.8)
#
# Designed to add <2s to a normal commit. Skips checks when none of the
# relevant files are staged.
#
# History: this hook exists because v0.95.8 shipped with a CI failure on
# env-example-drift (Phase 6 added 3 config knobs to settings.py without
# refreshing .env.example). The hook prevents that exact pattern from
# repeating.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Find the Python interpreter — prefer the project venv.
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PY="$REPO_ROOT/.venv/bin/python"
else
    PY="$(command -v python3)"
fi

STAGED="$(git diff --cached --name-only --diff-filter=ACMR || true)"

fail=0

# 1. env-example drift — runs when settings.py or .env.example is staged
if echo "$STAGED" | grep -qE '(config/settings\.py|\.env\.example)$'; then
    if ! PYTHONPATH=src/mcp "$PY" scripts/gen_env_example.py --check >/dev/null 2>&1; then
        echo "pre-commit: .env.example is out of sync with settings.py" >&2
        echo "  regenerate: PYTHONPATH=src/mcp $PY scripts/gen_env_example.py" >&2
        fail=1
    fi
fi

# 2. MCP description style — runs when any registered-tool source is staged
if echo "$STAGED" | grep -qE '(src/mcp/app/(tools\.py|mcp_tools/.*\.py)|docs/MCP_TOOL_STYLE\.md)$'; then
    if ! PYTHONPATH=src/mcp "$PY" scripts/lint-mcp-descriptions.py >/dev/null 2>&1; then
        echo "pre-commit: MCP tool descriptions failed style check" >&2
        echo "  diagnose: PYTHONPATH=src/mcp $PY scripts/lint-mcp-descriptions.py" >&2
        echo "  style guide: docs/MCP_TOOL_STYLE.md" >&2
        fail=1
    fi
fi

if [ "$fail" -ne 0 ]; then
    echo "" >&2
    echo "pre-commit: blocking commit. Fix the issues above, restage, retry." >&2
    echo "(emergency bypass: git commit --no-verify — only if you know what you're doing)" >&2
    exit 1
fi

exit 0
