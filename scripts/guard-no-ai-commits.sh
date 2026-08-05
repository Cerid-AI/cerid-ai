#!/usr/bin/env bash
# Reject commits authored, committed, or trailer-attributed to an AI assistant.
#
# Dual purpose:
#   1. Enforces the repo's no-AI-attribution commit policy.
#   2. Detects the IronWorm npm worm's propagation signature — it pushes
#      backdated commits authored as `claude <claude@users.noreply.github.com>`
#      (JFrog, 2026-06-04). Any such commit in a cerid repo is a red flag.
#
# Usage: guard-no-ai-commits.sh [<git-rev-range>]
# Default range: commits on HEAD not in origin/main; else whole history (--all).
# bash 3.2-safe (macOS) — uses git-native --author/--committer/--grep filters.
set -euo pipefail

RANGE="${1:-}"
if [ -z "$RANGE" ]; then
  if git rev-parse --verify -q origin/main >/dev/null 2>&1; then
    RANGE="origin/main..HEAD"
  else
    RANGE="--all"
  fi
fi

IDENT='claude|anthropic|noreply@anthropic'
ident=$(
  {
    git log "$RANGE" --no-merges -i --extended-regexp --author="$IDENT" \
      --format='%H  author=%an <%ae>'
    git log "$RANGE" --no-merges -i --extended-regexp --committer="$IDENT" \
      --format='%H  committer=%cn <%ce>'
  } | sort -u
)
msg=$(
  git log "$RANGE" --no-merges -i --extended-regexp \
    --grep='co-authored-by:.*(claude|anthropic)' \
    --grep='🤖 generated with' \
    --grep='generated with .*claude' \
    --grep='noreply@anthropic\.com' \
    --format='%H  %s'
)

# --- backdating detection -------------------------------------------------
# IronWorm copies an old timestamp onto a freshly-created commit so it "looks"
# like it was made whenever the project was last legitimately touched. Two
# date-based signals catch that even if a variant spoofs the human's identity:
#   A) committer-date earlier than the commit's first parent (DAG time-travel)
#   B) author/committer dates diverging by more than GUARD_SKEW_MAX_DAYS
SKEW_MAX_DAYS="${GUARD_SKEW_MAX_DAYS:-180}"
backdated=""
while read -r h at ct; do
  [ -z "$h" ] && continue
  if [ "$at" -gt "$ct" ]; then d=$((at - ct)); else d=$((ct - at)); fi
  if [ "$d" -gt $((SKEW_MAX_DAYS * 86400)) ]; then
    backdated="${backdated}${h}  author/committer dates diverge by $((d / 86400))d
"
  fi
done < <(git log "$RANGE" --no-merges --format='%H %at %ct')
while read -r h ct parents; do
  fp=${parents%% *}; [ -z "$fp" ] && continue
  pct=$(git show -s --format=%ct "$fp" 2>/dev/null) || continue
  if [ -n "$pct" ] && [ "$ct" -lt "$pct" ]; then
    backdated="${backdated}${h}  commit-date precedes its parent by $(((pct - ct) / 3600))h
"
  fi
done < <(git log "$RANGE" --no-merges --format='%H %ct %P')

# --- committed-payload detection ------------------------------------------
# The ChainDrop / Shai-Hulud npm worm class (2026-08-04 keyv/cacheable) persists
# by COMMITTING files, which every check above structurally cannot see — they
# all read commit metadata, never content. The payload is a preinstall loader
# (setup.mjs) plus editor hooks that re-execute it on open: a SessionStart entry
# in .claude/settings.json and a task in .vscode/tasks.json.
# Swept 2026-08-04: absent from both repos. Detected here so a variant cannot
# land quietly. Procedure: docs/RUNBOOK_INCIDENTS.md § npm supply-chain worm sweep.
IOC_NAMES='(^|/)(setup\.mjs|math_init\.js|Math_Symbol\.js)$'
if [ "$RANGE" = "--all" ]; then
  ioc_files=$(git ls-files | grep -E "$IOC_NAMES" || true)
  ioc_hooks=""
else
  ioc_files=$(git diff --name-only --diff-filter=AM "$RANGE" | grep -E "$IOC_NAMES" || true)
  # A hook config newly invoking a JS runtime. Legitimate hooks in this fleet
  # are all "$CLAUDE_PROJECT_DIR"/.claude/hooks/*.sh — never node/bun.
  ioc_hooks=$(git diff "$RANGE" -- '*.claude/settings.json' '*.vscode/tasks.json' \
    | grep -E '^\+.*\b(node|bun|npx)\b' || true)
fi
# A preinstall script is the worm's npm-side execution trigger.
if [ "$RANGE" != "--all" ]; then
  # Not anchored after the `+`: a minified package.json carries "preinstall"
  # mid-line, and the probe proved an anchored form misses exactly that shape.
  ioc_preinstall=$(git diff "$RANGE" -- '*package.json' \
    | grep -E '^\+.*"preinstall"[[:space:]]*:' || true)
else
  ioc_preinstall=""
fi

if [ -z "$ident" ] && [ -z "$msg" ] && [ -z "$backdated" ] \
   && [ -z "$ioc_files" ] && [ -z "$ioc_hooks" ] && [ -z "$ioc_preinstall" ]; then
  echo "✓ no AI-authored/attributed/backdated commits or worm payloads in range '$RANGE'"
  exit 0
fi

echo "✗ suspicious commit(s) detected — blocked (no-AI-attribution policy + npm-worm IOCs):"
[ -n "$ident" ] && printf '%s\n' "$ident"        | sed 's/^/  identity   /'
[ -n "$msg" ]   && printf '%s\n' "$msg"           | sed 's/^/  message    /'
[ -n "$backdated" ] && printf '%s' "$backdated"   | sed 's/^/  backdated  /'
[ -n "$ioc_files" ] && printf '%s\n' "$ioc_files" | sed 's/^/  worm-file  /'
[ -n "$ioc_hooks" ] && printf '%s\n' "$ioc_hooks" | sed 's/^/  worm-hook  /'
[ -n "$ioc_preinstall" ] && printf '%s\n' "$ioc_preinstall" | sed 's/^/  preinstall /'
echo
echo "Commits must be authored solely by the human developer, with honest dates."
echo "False positive? Narrow IDENT or raise GUARD_SKEW_MAX_DAYS in this script."
exit 1
