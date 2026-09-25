#!/bin/bash
# .claude/hooks/stop-verify.sh
# Stop hook: the gate CLAUDE.md <verification> describes. When Claude ends a
# turn in which files differ from HEAD, run the repo's lint + typecheck once
# and block (exit 2, stderr back to Claude) until they pass, so "done" means
# green. PostToolUse hooks stay advisory — they exit 0 and see one file.
#
# CHECKS: "path-regex::command", one per entry. A command runs when any file
# that differs from HEAD (or is untracked) matches its regex. Commands run
# from the repo root. Edit this table per repo; keep the rest generic.
#
# A fingerprint of the tree (HEAD + diff + untracked blobs) is kept under
# $TMPDIR so an unchanged tree — a question-only turn while edits sit
# uncommitted — never re-pays minutes of mypy/tsc. MAX_BLOCKS consecutive
# failing runs yield with a warning instead of looping the turn forever.
#
# stdin: {"stop_hook_active": bool, ...} — drained, unused: the fail counter
# below is the loop guard, so a fix made after a block still gets verified.

CHECKS=(
  'src/mcp/.*\.py$::.venv/bin/ruff check src/mcp/ && .venv/bin/mypy src/mcp/'
  'src/web/.*\.(ts|tsx|js|mjs|json)$::cd src/web && npx eslint . && npx tsc -b'
)
MAX_BLOCKS=2

cat >/dev/null
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$ROOT" 2>/dev/null || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

CHANGED=$( { git diff --name-only HEAD --diff-filter=ACMR; git ls-files --others --exclude-standard; } 2>/dev/null | sort -u)
[ -n "$CHANGED" ] || exit 0

FP=$( { git rev-parse HEAD; git diff HEAD; git ls-files --others --exclude-standard | git hash-object --stdin-paths; } 2>/dev/null | shasum -a 256 | cut -c1-16)
STATE_DIR="${TMPDIR:-/tmp}/claude-stop-verify"
mkdir -p "$STATE_DIR"
STATE="$STATE_DIR/$(printf '%s' "$ROOT" | shasum -a 256 | cut -c1-16)"
LAST_FP=""; LAST_STATUS=""; LAST_BLOCKS=0
[ -f "$STATE" ] && read -r LAST_FP LAST_STATUS LAST_BLOCKS < "$STATE"
[ "$FP" = "$LAST_FP" ] && [ "$LAST_STATUS" = "pass" ] && exit 0

BLOCKS=$LAST_BLOCKS
[ "$LAST_STATUS" = "fail" ] || BLOCKS=0
if [ "$FP" = "$LAST_FP" ] && [ "$LAST_STATUS" = "fail" ]; then
  # Same tree, already reported. Re-emit the cached report; do not re-run.
  OUT=$(cat "$STATE.out" 2>/dev/null)
  RC=1
else
  OUT=""; RC=0; RAN=0
  for entry in "${CHECKS[@]}"; do
    re="${entry%%::*}"; cmd="${entry#*::}"
    printf '%s\n' "$CHANGED" | grep -qE "$re" || continue
    RAN=1
    if ! step=$(cd "$ROOT" && bash -c "$cmd" 2>&1); then
      RC=1
      OUT+="── FAILED: $cmd"$'\n'"$(printf '%s\n' "$step" | tail -60)"$'\n'
    fi
  done
  [ "$RAN" = 1 ] || exit 0
fi

if [ "$RC" = 0 ]; then
  printf '%s pass 0\n' "$FP" > "$STATE"
  rm -f "$STATE.out"
  exit 0
fi

BLOCKS=$((BLOCKS + 1))
printf '%s fail %s\n' "$FP" "$BLOCKS" > "$STATE"
printf '%s' "$OUT" > "$STATE.out"

if [ "$BLOCKS" -gt "$MAX_BLOCKS" ]; then
  {
    echo "[stop-verify] still failing after $MAX_BLOCKS fix attempts — yielding to the operator."
    echo "Report the failure verbatim; do not claim the change is verified."
    printf '%s\n' "$OUT"
  } >&2
  exit 0
fi

{
  echo "[stop-verify] lint/typecheck failed (attempt $BLOCKS of $MAX_BLOCKS). Fix before finishing;"
  echo "do not report the work as done or verified until this gate passes."
  printf '%s\n' "$OUT"
} >&2
exit 2
