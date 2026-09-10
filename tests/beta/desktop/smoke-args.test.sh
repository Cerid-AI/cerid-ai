#!/bin/bash
# Contract tests for tests/beta/desktop-smoke.sh that need no packaged app.
#
# The smoke itself can only run on a Mac with Cerid AI.app installed, so the
# parts that are always testable — argument handling, the app-absent failure,
# and the ordering that keeps --reset from touching real state when the app is
# missing — are pinned here instead of being left to the one machine that can
# run the full smoke.
#
# Every case points $CERID_APP at a path that does not exist, so no case can
# launch the real app or take the CDP port.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SMOKE="${SCRIPT_DIR}/../desktop-smoke.sh"
ABSENT_APP="/nonexistent/Nope.app"

FAILURES=0
check() {
  local name="$1" cond="$2" detail="${3:-}"
  if [[ "$cond" == "true" ]]; then
    echo "PASS ${name}"
  else
    echo "FAIL ${name}${detail:+ — ${detail}}"
    FAILURES=$((FAILURES + 1))
  fi
}

# Each case gets a throwaway HOME so a regression in the --reset ordering
# deletes a temp file rather than this Mac's real connection.json.
new_home() {
  local h
  h="$(mktemp -d)"
  mkdir -p "${h}/Library/Application Support/cerid-desktop/secrets"
  echo '{"marker":"must-survive"}' > "${h}/Library/Application Support/cerid-desktop/connection.json"
  echo "$h"
}

# --- app absent: loud, non-zero, and never a green ---
H="$(new_home)"
OUT="$(HOME="$H" CERID_APP="$ABSENT_APP" bash "$SMOKE" 2>&1)"
RC=$?
check "absent-app-exit-nonzero" "$([[ $RC -ne 0 ]] && echo true || echo false)" "exit was ${RC}"
check "absent-app-no-pass-lines" "$(grep -q '^PASS ' <<<"$OUT" && echo false || echo true)" "printed a PASS while unable to run"
check "absent-app-names-path" "$(grep -q "$ABSENT_APP" <<<"$OUT" && echo true || echo false)" "message does not name the bundle it looked for"
# PlistBuddy chatters on stdout for a missing plist; that text must not end up
# inside the executable path the message reports.
check "absent-app-message-clean" "$(grep -qE "Will Create|Doesn't Exist" <<<"$OUT" && echo false || echo true)" "PlistBuddy noise leaked into the failure message"
rm -rf "$H"

# --- --help: usage, exit 0, no launch attempt ---
H="$(new_home)"
OUT="$(HOME="$H" CERID_APP="$ABSENT_APP" bash "$SMOKE" --help 2>&1)"
RC=$?
check "help-exit-zero" "$([[ $RC -eq 0 ]] && echo true || echo false)" "exit was ${RC}"
check "help-prints-usage" "$(grep -qi 'usage' <<<"$OUT" && echo true || echo false)" "no usage line"
check "help-does-not-probe-app" "$(grep -q 'FAIL launch' <<<"$OUT" && echo false || echo true)" "--help fell through to the app check"
rm -rf "$H"

# --- unknown argument: rejected, not silently ignored ---
H="$(new_home)"
OUT="$(HOME="$H" CERID_APP="$ABSENT_APP" bash "$SMOKE" --bogus-typo 2>&1)"
RC=$?
check "unknown-arg-exit-nonzero" "$([[ $RC -ne 0 ]] && echo true || echo false)" "exit was ${RC}"
check "unknown-arg-named" "$(grep -q -- '--bogus-typo' <<<"$OUT" && echo true || echo false)" "the rejected argument is not named"
# The absent app would exit non-zero on its own, so the exit code alone proves
# nothing here; the argument has to be rejected before the app is ever probed.
check "unknown-arg-rejected-before-app-probe" "$(grep -q 'FAIL launch' <<<"$OUT" && echo false || echo true)" "unknown argument fell through to the app check instead of being rejected"
rm -rf "$H"

# --- --reset must not touch state when the app is absent ---
H="$(new_home)"
HOME="$H" CERID_APP="$ABSENT_APP" bash "$SMOKE" --reset >/dev/null 2>&1
check "reset-guarded-by-app-check" \
  "$([[ -f "${H}/Library/Application Support/cerid-desktop/connection.json" ]] && echo true || echo false)" \
  "--reset deleted real state even though the app was missing"
rm -rf "$H"

echo ""
if [[ $FAILURES -gt 0 ]]; then
  echo "${FAILURES} check(s) failed"
  exit 1
fi
echo "all checks passed"
exit 0
