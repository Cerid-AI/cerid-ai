#!/bin/bash
# .claude/hooks/typecheck.sh
# PostToolUse hook: runs TypeScript type-check after editing .ts/.tsx files
# Receives tool input as JSON on stdin
#
# MUST be `tsc -b`, never `tsc --noEmit`.
#
# src/web/tsconfig.json is a Vite *solution* file — `"files": []` plus project
# `references`. `tsc --noEmit` against it compiles ZERO files and always exits 0
# (`npx tsc --noEmit --listFiles | wc -l` → 0). This hook shipped with
# `--noEmit`, so every "type check passed" it reported was vacuous and real type
# errors reached `npm run build` (= `tsc -b && vite build`) in CI and the Docker
# image instead. The Makefile was corrected for this on 2026-06-14
# (tasks/lessons.md); the hook was missed until the 2026-07-30 graduation sweep.
#
# Both referenced projects set `noEmit: true` and write `tsBuildInfoFile` under
# node_modules/.tmp/, so `-b` type-checks incrementally without emitting JS or
# dirtying the tree.

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only check TypeScript files in src/web/
if [[ "$FILE_PATH" == *.ts || "$FILE_PATH" == *.tsx ]]; then
  if [[ "$FILE_PATH" == *"/src/web/"* ]]; then
    cd "$CLAUDE_PROJECT_DIR/src/web" || exit 0
    # `-b` walks the referenced projects (app + node), type-checking app AND
    # test files — the same surface CI's `frontend` job and the Docker build use.
    if ! npx tsc -b 2>&1; then
      echo "TypeScript type check found errors (tsc -b)" >&2
    fi
  fi
fi

exit 0
