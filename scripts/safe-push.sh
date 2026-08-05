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

# A dirty tree would mean validating something other than what ships. Refuse
# rather than record a result that describes the wrong content — the whole
# point of the record is that it vouches for a specific commit.
if [ -n "$(git status --porcelain)" ]; then
  echo "safe-push: ✗ working tree is dirty."
  echo "  Validation would run against the working tree while the push ships HEAD,"
  echo "  so the result would not describe what you are pushing. Commit or stash first."
  git status --short | head -20
  exit 1
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
