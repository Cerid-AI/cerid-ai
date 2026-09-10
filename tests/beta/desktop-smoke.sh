#!/bin/bash
# Cerid AI Beta — Packaged Desktop App Smoke
#
# Drives a real installed Cerid AI.app through first-run, over the Chrome
# DevTools Protocol (tests/beta/desktop/first-run.cdp.mjs). Opt-in only —
# it needs a macOS install of the app and the personal stack on :8888; it
# is not part of tests/beta/run.sh's default tiers or --full.
#
# Usage:
#   ./tests/beta/desktop-smoke.sh            # smoke the installed app
#   ./tests/beta/desktop-smoke.sh --reset    # also wipe this Mac's saved
#                                             # connection and setup-complete
#                                             # state first, to force the
#                                             # first-run flow
#
# $CERID_APP overrides the app bundle path (default: /Applications/Cerid AI.app).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPORTS_DIR="${SCRIPT_DIR}/reports"

usage() {
  cat <<'USAGE'
Usage: desktop-smoke.sh [--reset] [--help]

  --reset   Remove this Mac's saved connection.json, secrets/, and Local
            Storage (where the cerid-desktop-setup-complete flag lives,
            independent of the connection state) before the run, to force
            the packaged app back through its first-run flow. Only ever
            acts on an app that is actually installed.
  --help    Show this message.

$CERID_APP overrides the app bundle path (default: /Applications/Cerid AI.app).
USAGE
}

# Arguments are validated before anything else so a typo ("--rest") is rejected
# rather than silently running without the flag the operator meant to pass.
# This has to precede the app probe, or the only feedback on a machine without
# the app installed would be the launch failure.
RESET=false
for arg in "$@"; do
  case "$arg" in
    --reset) RESET=true ;;
    --help|-h) usage; exit 0 ;;
    *)
      echo "desktop-smoke: unknown argument '${arg}'" >&2
      usage >&2
      exit 2
      ;;
  esac
done

mkdir -p "$REPORTS_DIR"

APP_PATH="${CERID_APP:-/Applications/Cerid AI.app}"
# PlistBuddy reports a missing plist on stdout, not stderr, so 2>/dev/null does
# not stop that text from being captured as the bundle's executable name. Only
# ask it when the plist is actually there; otherwise the failure message below
# reports a path with PlistBuddy's chatter embedded in it.
PLIST_PATH="${APP_PATH}/Contents/Info.plist"
BIN_NAME=""
if [[ -f "$PLIST_PATH" ]]; then
  BIN_NAME="$(/usr/libexec/PlistBuddy -c "Print :CFBundleExecutable" "$PLIST_PATH" 2>/dev/null | head -n 1)"
fi
[[ -n "$BIN_NAME" ]] || BIN_NAME="Cerid AI"
BIN_PATH="${APP_PATH}/Contents/MacOS/${BIN_NAME}"
PKILL_PATTERN="${APP_PATH}/Contents/MacOS/${BIN_NAME}"

if [[ ! -x "$BIN_PATH" ]]; then
  echo "FAIL launch — no executable at ${BIN_PATH}" >&2
  echo "  The packaged app is not installed at ${APP_PATH}." >&2
  echo "  Install it, or set \$CERID_APP to a built bundle. This smoke drives a" >&2
  echo "  packaged app only; it cannot run against a dev server." >&2
  exit 2
fi

# Same resolution as run.sh: env wins, fall back to the repo .env.
CERID_API_KEY="${CERID_API_KEY:-$(grep -E '^CERID_API_KEY=' "${REPO_ROOT}/.env" 2>/dev/null | cut -d= -f2-)}"
export CERID_API_KEY

APP_SUPPORT_DIR="${HOME}/Library/Application Support/cerid-desktop"

if $RESET; then
  # Local Storage holds the renderer's cerid-desktop-setup-complete flag
  # (desktop-setup.tsx), which is independent of connection.json — a Mac
  # can carry "setup already complete" from a prior run even when the
  # connection itself was never actually saved (Task A1's bug), and that
  # flag alone is enough to skip the first-run wizard on the next launch.
  echo "--reset: removing ${APP_SUPPORT_DIR}/connection.json, its secrets/ directory, and its Local Storage"
  rm -f "${APP_SUPPORT_DIR}/connection.json"
  rm -rf "${APP_SUPPORT_DIR}/secrets"
  rm -rf "${APP_SUPPORT_DIR}/Local Storage"
fi

quit_app() {
  pkill -f "$PKILL_PATTERN" 2>/dev/null || true
  for _ in $(seq 1 20); do
    pgrep -f "$PKILL_PATTERN" >/dev/null 2>&1 || return 0
    sleep 0.5
  done
  pkill -9 -f "$PKILL_PATTERN" 2>/dev/null || true
}

launch_app() {
  local log="$1"
  "$BIN_PATH" --remote-debugging-port=9222 > "$log" 2>&1 &
  disown
}

# Belt-and-suspenders: a stale instance from a previous run would already
# hold the debugging port and connection.json, which would make every
# check below observe the WRONG process. Never leave one running.
quit_app

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   DESKTOP PACKAGED-APP SMOKE (CDP)           ║"
echo "╚══════════════════════════════════════════════╝"
echo "App:  ${APP_PATH}"
echo "Reset: ${RESET}"
echo ""

LOG1="${REPORTS_DIR}/desktop-smoke-launch1-$(date +%Y%m%d-%H%M%S).log"
RESULTS1="${REPORTS_DIR}/desktop-smoke-checks1.results"
echo "Launching (log: ${LOG1})..."
launch_app "$LOG1"

node "${SCRIPT_DIR}/desktop/first-run.cdp.mjs" --stage=initial 2>&1 | tee "$RESULTS1"
EXIT1=${PIPESTATUS[0]}

echo ""
echo "Quitting and relaunching for the relaunch check..."
quit_app

LOG2="${REPORTS_DIR}/desktop-smoke-launch2-$(date +%Y%m%d-%H%M%S).log"
RESULTS2="${REPORTS_DIR}/desktop-smoke-checks2.results"
launch_app "$LOG2"

node "${SCRIPT_DIR}/desktop/first-run.cdp.mjs" --stage=post-relaunch 2>&1 | tee "$RESULTS2"
EXIT2=${PIPESTATUS[0]}

quit_app

echo ""
PASS_COUNT=$(cat "$RESULTS1" "$RESULTS2" 2>/dev/null | grep -c '^PASS ' || true)
FAIL_COUNT=$(cat "$RESULTS1" "$RESULTS2" 2>/dev/null | grep -c '^FAIL ' || true)
SKIP_COUNT=$(cat "$RESULTS1" "$RESULTS2" 2>/dev/null | grep -c '^SKIP ' || true)
echo "Desktop smoke results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed, ${SKIP_COUNT} skipped"

if [[ $EXIT1 -ne 0 || $EXIT2 -ne 0 ]]; then
  exit 1
fi
exit 0
