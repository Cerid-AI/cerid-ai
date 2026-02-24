#!/bin/bash
# .claude/hooks/typecheck.sh
# PostToolUse hook: runs TypeScript type-check after editing .ts/.tsx files
# Receives tool input as JSON on stdin

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only check TypeScript files in src/web/
if [[ "$FILE_PATH" == *.ts || "$FILE_PATH" == *.tsx ]]; then
  if [[ "$FILE_PATH" == *"/src/web/"* ]]; then
    cd "$CLAUDE_PROJECT_DIR/src/web" || exit 0
    # Run tsc in noEmit mode — output errors for Claude to see
    npx tsc --noEmit 2>&1
    EXIT=$?
    if [ $EXIT -ne 0 ]; then
      echo "TypeScript type check found errors" >&2
    fi
  fi
fi

exit 0
