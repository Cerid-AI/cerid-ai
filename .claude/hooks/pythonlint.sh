#!/bin/bash
# .claude/hooks/pythonlint.sh
# PostToolUse hook: runs ruff check after editing .py files in src/mcp/
# Receives tool input as JSON on stdin

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only check Python files in src/mcp/
if [[ "$FILE_PATH" == *.py ]]; then
  if [[ "$FILE_PATH" == *"/src/mcp/"* ]]; then
    # Run ruff via Docker (host Python may lack ruff)
    if command -v ruff &>/dev/null; then
      ruff check "$FILE_PATH" 2>&1
      EXIT=$?
      if [ $EXIT -ne 0 ]; then
        echo "ruff found lint issues" >&2
      fi
    else
      # Fallback: try ruff from pip3
      python3 -m ruff check "$FILE_PATH" 2>&1 || true
    fi
  fi
fi

exit 0
