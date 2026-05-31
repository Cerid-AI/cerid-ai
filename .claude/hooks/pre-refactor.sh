#!/bin/bash
# .claude/hooks/pre-refactor.sh
# PreToolUse hook: intercepts refactor / architecture change language in Cerid repos.
# Forces a preservation gate and recommends multi-step review process.
#
# Complements the Grok-side pre-refactor.md hook for dual-agent consistency.

set -euo pipefail

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // .tool_input.prompt // empty' 2>/dev/null || echo "$INPUT")

REFACTOR_REGEX='refactor|restructure|move code between|clean up architecture|improve separation|layering change|core.*app|app.*core'

if echo "$PROMPT" | grep -qiE "$REFACTOR_REGEX"; then
  echo "PRE-REFACTOR SAFETY (Claude): Major architecture or layering change detected." >&2
  echo "Mandatory steps before proceeding:" >&2
  echo "  1. Run preservation-check (make preservation-check or equivalent)." >&2
  echo "  2. Use the Preservation Guard persona as a dedicated reviewer in any implement/review loop." >&2
  echo "  3. Produce a short Refactor Safety Brief (risks, affected areas, recommended order)." >&2
  echo "  4. Break large refactors into smaller, reviewable pieces where possible." >&2
  echo "" >&2
  echo "Protect the sacred core/app separation and DI agent boundaries." >&2
fi

exit 0
