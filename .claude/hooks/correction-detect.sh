#!/bin/bash
# .claude/hooks/correction-detect.sh
# UserPromptSubmit hook: when the user's message looks like a correction,
# append a reminder to tasks/lessons.md so the lesson is captured even if
# Claude forgets the prose <self_improvement> rule under context pressure.
#
# Receives the prompt as JSON on stdin: {"prompt": "..."}.
# Exits 0 always — this hook only writes a side-channel reminder; it must
# never block the user prompt.

set -euo pipefail

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')

# Correction patterns — match case-insensitively at the start of the message
# or after a leading address ("hey claude, no that's wrong"). Tuned to be
# precise rather than greedy; false-positives cost a stub line, false-negatives
# cost a lost lesson.
CORRECTION_REGEX='^[[:space:]]*(no\b|stop\b|don'\''?t\b|wrong\b|actually,|that'\''?s wrong|incorrect\b|not what)'

if echo "$PROMPT" | grep -qiE "$CORRECTION_REGEX"; then
  PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
  LESSONS_FILE="$PROJECT_DIR/tasks/lessons.md"

  mkdir -p "$(dirname "$LESSONS_FILE")"

  # Stub entry — Claude reads tasks/lessons.md at appropriate moments and
  # fills in the actual lesson. The timestamp + truncated prompt is the
  # cue, not the lesson itself.
  TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
  SNIPPET=$(echo "$PROMPT" | head -c 200 | tr '\n' ' ')

  {
    echo ""
    echo "## $TIMESTAMP — correction pending review"
    echo ""
    echo "> User correction: \"$SNIPPET...\""
    echo ""
    echo "_Stub created by correction-detect hook. Claude: capture the pattern, the rule that prevents it, and the reason._"
  } >> "$LESSONS_FILE"
fi

exit 0
