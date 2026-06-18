#!/bin/bash
# .claude/hooks/preservation-check.sh
# PreToolUse / UserPromptSubmit hook for Cerid AI monorepos.
# Strongly enforces the core architecture preservation rules before any structural work.
#
# Triggers on keywords or file patterns related to refactoring, layering, core/app, DI agents, etc.
# Outputs guidance for the model and recommends running the preservation check + using the
# Preservation Guard reviewer persona.
#
# Non-blocking by default (prints warnings/guidance). Matches the protective intent of the
# corresponding .grok/hooks versions.

set -euo pipefail

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // .tool_input.prompt // empty' 2>/dev/null || echo "$INPUT")

# Triggers for preservation-sensitive work
PRESERVATION_REGEX='refactor|restructure|move code|layering|core/|app/|import-linter|preservation|DI agent|dependency injection|architectural change|boundary violation'

if echo "$PROMPT" | grep -qiE "$PRESERVATION_REGEX"; then
  echo "PRESERVATION ALERT (Claude): This request touches architecture or layering invariants." >&2
  echo "Recommended actions:" >&2
  echo "  1. Run: make preservation-check (or the equivalent import-linter + architecture tests)" >&2
  echo "  2. Fork the 'Preservation Guard' reviewer persona (or equivalent) for any proposed changes." >&2
  echo "  3. Do not proceed with structural edits until the check passes or the user explicitly accepts the risk." >&2
  echo "" >&2
  echo "Core rules to enforce: core/ must never import app/, DI-threaded agents belong in core/agents/, routers are billing-only." >&2
fi

exit 0
