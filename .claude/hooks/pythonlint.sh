#!/bin/bash
# .claude/hooks/pythonlint.sh
# PostToolUse hook: runs ruff check after editing .py files in src/mcp/
# Receives tool input as JSON on stdin

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only check Python files in src/mcp/
if [[ "$FILE_PATH" == *.py ]]; then
  if [[ "$FILE_PATH" == *"/src/mcp/"* ]]; then
    if command -v ruff &>/dev/null; then
      RUFF=ruff
    elif python3 -m ruff --version &>/dev/null 2>&1; then
      RUFF="python3 -m ruff"
    else
      exit 0
    fi

    # Auto-fix safe issues (imports, trailing whitespace, etc.)
    $RUFF check --fix --select I,W,UP "$FILE_PATH" 2>/dev/null

    # Report remaining issues
    $RUFF check "$FILE_PATH" 2>&1
    if [ $? -ne 0 ]; then
      echo "ruff found lint issues" >&2
    fi
  fi
fi

exit 0
