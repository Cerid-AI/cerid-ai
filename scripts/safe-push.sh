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

# An ANNOTATED TAG pushes the tag OBJECT's sha, not the commit's, so a record
# holding only the commit can never match and the hook falls through to
# validating inside itself — the path that holds the connection open and dies
# with SIGPIPE. Cutting v1.0.2 hit exactly that. Record every tag object that
# points at the validated commit alongside it; the supply-chain guard still
# runs on every push regardless, since it is consulted before the record.
for _tag in $(git tag --points-at "$SHA"); do
  _tag_obj="$(git rev-parse "$_tag")"
  if [ "$_tag_obj" != "$SHA" ]; then
    echo "$_tag_obj" >> "$STAMP"
    echo "── safe-push: also recording annotated tag $_tag ──"
  fi
done

echo "── safe-push: ✓ validated — pushing ──"

# WHY THIS DOES NOT END AT `git push`
#
# Validation passing and the push landing are different events, and this script
# used to report only the first. `git push` can die at the TRANSPORT after the
# hook returns — SIGPIPE, exit 141 — and the run then reads as "validated,
# pushing" with nothing on the remote. That happened twice on 2026-08-31 and
# once on the v1.0.3-desktop tag, where it went unnoticed until `git ls-remote`
# was checked by hand and the release build never fired.
#
# So: retry once, then VERIFY the remote actually moved. The retry is cheap in
# a way it would not have been before — the record is already written, so the
# hook returns in seconds instead of re-running the full gate.
_push() {
  if [ "$#" -gt 0 ]; then
    git push "$@"
  else
    git push origin "$(git rev-parse --abbrev-ref HEAD)"
  fi
}

if ! _push "$@"; then
  rc=$?
  echo "── safe-push: ⚠ push exited $rc — retrying once ──"
  echo "   (141 is SIGPIPE: the transport dropped, not a rejected push. The"
  echo "    validation record still stands, so this retry is seconds, not minutes.)"
  if ! _push "$@"; then
    echo "── safe-push: ✗ push failed twice — nothing landed ──" >&2
    exit 1
  fi
fi

# Verify rather than assume. Only for the shapes whose target ref is
# unambiguous: no arguments (current branch) or `<remote> <ref>` with no
# refspec colon. Anything else — multiple refs, --delete, an explicit
# src:dst — is reported as unverified rather than guessed at, because a
# confident wrong answer here is worse than none.
_remote=""; _ref=""
if [ "$#" -eq 0 ]; then
  _remote="origin"; _ref="$(git rev-parse --abbrev-ref HEAD)"
elif [ "$#" -eq 2 ] && [ "${2#-}" = "$2" ] && [ "${2#*:}" = "$2" ]; then
  _remote="$1"; _ref="$2"
elif [ "$#" -eq 3 ] && [ "${2#-}" = "$2" ] && [ "${2#*:}" = "$2" ]; then
  # e.g. `origin branch --force-with-lease`
  _remote="$1"; _ref="$2"
fi

if [ -n "$_ref" ]; then
  _local="$(git rev-parse "$_ref" 2>/dev/null || true)"
  _remote_sha="$(git ls-remote "$_remote" "$_ref" 2>/dev/null | grep -v '\^{}' | head -1 | cut -f1)"
  if [ -z "$_remote_sha" ]; then
    echo "── safe-push: ✗ $_ref is NOT on $_remote after a push that reported success ──" >&2
    exit 1
  fi
  if [ "$_local" != "$_remote_sha" ]; then
    echo "── safe-push: ✗ $_remote/$_ref is $(printf '%.12s' "$_remote_sha"), expected $(printf '%.12s' "$_local") ──" >&2
    echo "   The push reported success but the remote does not match. Re-run." >&2
    exit 1
  fi
  echo "── safe-push: ✓ verified $_remote/$_ref = $(printf '%.12s' "$_remote_sha") ──"
else
  echo "── safe-push: ⚠ pushed, but the target ref could not be determined from"
  echo "   these arguments, so the remote was NOT verified. Check with git ls-remote."
fi
