#!/usr/bin/env bash
# safe-push — validate first, then push.
#
#   make push                  # → origin, current branch
#   scripts/safe-push.sh       # same
#   scripts/safe-push.sh origin main --force-with-lease
#
# WHY THIS EXISTS
#
# `git push` opens the connection to the remote BEFORE running the pre-push
# hook, so a hook that validates for minutes leaves that connection idle and
# GitHub closes it: the gate passes, git says "pushing", and the transfer dies
# with "Connection to github.com closed by remote host". That cost five failed
# pushes across three repos on 2026-08-04/05, and the usual workaround —
# `git push --no-verify` — silently skips the supply-chain guard too, trading a
# security check for a transport problem.
#
# This runs the SAME validation the hook runs (`pre-push --validate-only`, one
# definition, two callers, so the two can never drift apart), records the
# commit that passed, and only then pushes. The hook sees the record and
# returns in seconds, so nothing sits idle. The guard still runs in the hook,
# every time, and is never covered by the record.

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

HOOK="scripts/hooks/pre-push"
STAMP=".git/prepush-validated"

[ -f "$HOOK" ] || { echo "safe-push: $HOOK not found"; exit 1; }

# Two kinds of dirty, with different consequences — collapsing them was the defect.
#
# TRACKED modifications mean validation would run against content that is not what
# ships. Always refuse: the record's whole value is that it vouches for one commit.
#
# UNTRACKED files do not change what ships, but they DO change what validation SEES.
# The dangerous direction is not the obvious one: an untracked module can satisfy an
# import that HEAD alone cannot, turning a run that should fail into a pass. So this
# refuses too — but it names the files and offers an explicit override, because the
# workaround it replaces (hand-writing the stamp) skips the check silently, and a
# documented escape beats an undocumented one.
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "safe-push: ✗ tracked files are modified."
  echo "  Validation would run against the working tree while the push ships HEAD,"
  echo "  so the result would not describe what you are pushing. Commit or stash first."
  git status --short | head -20
  exit 1
fi

UNTRACKED="$(git ls-files --others --exclude-standard)"
if [ -n "$UNTRACKED" ]; then
  if [ "${SAFE_PUSH_ALLOW_UNTRACKED:-}" = "1" ]; then
    echo "safe-push: ⚠ untracked files present, continuing (SAFE_PUSH_ALLOW_UNTRACKED=1):"
    printf '  %s\n' $UNTRACKED | head -20
    echo "  These do not ship, but they ARE visible to the validation you are about to trust."
  else
    echo "safe-push: ✗ untracked files present."
    printf '  %s\n' $UNTRACKED | head -20
    echo "  They do not ship, but validation can see them — an untracked module can satisfy"
    echo "  an import HEAD alone cannot, so a run that should fail would pass."
    echo "  Remove or commit them, or re-run with SAFE_PUSH_ALLOW_UNTRACKED=1 if you have"
    echo "  confirmed none of them affect the build, the tests or the linters."
    exit 1
  fi
fi

SHA="$(git rev-parse HEAD)"
echo "── safe-push: validating $(printf '%.12s' "$SHA") before opening any connection ──"

# Clear any prior record up front: if validation fails or is interrupted, a
# stale record must never be left behind for the hook to trust.
rm -f "$STAMP"

if ! bash "$HOOK" --validate-only; then
  echo "── safe-push: ✗ validation failed — nothing pushed, no record written ──"
  exit 1
fi

echo "$SHA" > "$STAMP"
echo "── safe-push: ✓ validated — pushing ──"

if [ "$#" -gt 0 ]; then
  git push "$@"
else
  git push origin "$(git rev-parse --abbrev-ref HEAD)"
fi
