#!/bin/bash
# .claude/hooks/safety-check.sh
# PreToolUse hook: warns before destructive Bash commands
# Receives tool input as JSON on stdin; exit non-zero to block

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Patterns that warrant blocking (user gets prompted to approve)
DESTRUCTIVE_PATTERNS=(
  'rm -rf'
  'rm -fr'
  'git reset --hard'
  'git clean -f'
  'git checkout \.'
  'git restore \.'
  'docker volume rm'
  'docker system prune'
  'drop table'
  'drop database'
  'truncate table'
  '> /dev/'
  'mkfs\.'
  'dd if='
)

for pattern in "${DESTRUCTIVE_PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -qiE "$pattern"; then
    echo "BLOCKED: Destructive command detected: $pattern" >&2
    echo "Command: $COMMAND" >&2
    exit 2
  fi
done

exit 0
