#!/bin/bash
# .claude/hooks/pre-large-change.sh
# PreToolUse / notification hook for large, high-impact edits in Cerid repos.
# Encourages breaking work into reviewable pieces and running appropriate guards.
#
# Provides Claude-side parity with the Grok pre-large-change.md hook.

set -euo pipefail

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // .tool_input.prompt // empty' 2>/dev/null || echo "$INPUT")

# Broad triggers for scale or impact
LARGE_CHANGE_REGEX='big change|major update|large refactor|many files|high-impact|touching core|cross-cutting|overhaul'

if echo "$PROMPT" | grep -qiE "$LARGE_CHANGE_REGEX"; then
  echo "PRE-LARGE-CHANGE GUIDANCE (Claude): High-scope edit detected." >&2
  echo "Recommended protocol:" >&2
  echo "  1. Pause and assess: Can this be broken into smaller, independently reviewable pieces?" >&2
  echo "  2. Run relevant guards: preservation-check, typecheck, relevant lints, kb-curate if applicable." >&2
  echo "  3. Strongly consider using the full implement + multiple specialized reviewers loop (including Preservation Guard for architecture touchpoints)." >&2
  echo "  4. Document blast radius and risks before starting the main body of work." >&2
  echo "" >&2
  echo "This hook helps avoid the common failure mode of over-editing without proper verification." >&2
fi

exit 0
