#!/bin/bash
# Proves tests/beta/lib/assert.sh derives COMPOSE_PROJECT_NAME from the main
# checkout (via `git rev-parse --git-common-dir`), not the caller's cwd, so
# `docker compose ps` (smoke.sh's S-01) finds the shared running stack from
# any worktree — not just the worktrees already on disk. Creates a throwaway
# worktree with `git worktree add`, runs S-01 from inside it, and removes the
# worktree afterward.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

WORKTREE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cerid-compose-project-test.XXXXXX")"
rmdir "$WORKTREE_DIR"
RESULTS_FILE="$(mktemp "${TMPDIR:-/tmp}/cerid-compose-project-test-results.XXXXXX")"
: > "$RESULTS_FILE"

# shellcheck disable=SC2329 # invoked indirectly via the EXIT trap below
cleanup() {
  cd "$SCRIPT_DIR" || true
  git -C "$REPO_ROOT" worktree remove --force "$WORKTREE_DIR" >/dev/null 2>&1 || true
  rm -rf "$WORKTREE_DIR" "$RESULTS_FILE"
}
trap cleanup EXIT

git -C "$REPO_ROOT" worktree add --detach "$WORKTREE_DIR" HEAD >/dev/null

# Expected project name, computed independently of assert.sh: the main
# checkout's directory basename, resolved the same way from any worktree.
EXPECTED_PROJECT="$(basename "$(cd "$(git -C "$REPO_ROOT" rev-parse --git-common-dir)/.." && pwd)")"

cd "$WORKTREE_DIR" || exit 1

export RESULTS_FILE
# shellcheck source=lib/assert.sh
source "${WORKTREE_DIR}/tests/beta/lib/assert.sh"

if [[ "$COMPOSE_PROJECT_NAME" != "$EXPECTED_PROJECT" ]]; then
  echo "FAIL: COMPOSE_PROJECT_NAME='${COMPOSE_PROJECT_NAME}' from the throwaway worktree, expected '${EXPECTED_PROJECT}'" >&2
  exit 1
fi

# Pull just s01_check out of smoke.sh — sourcing the whole tier would also
# run every other check and exit at the bottom; this test only proves S-01's
# project-targeting fix.
S01_SRC="$(sed -n '/^s01_check() {/,/^}/p' "${WORKTREE_DIR}/tests/beta/smoke.sh")"
if [[ -z "$S01_SRC" ]]; then
  echo "FAIL: could not extract s01_check from smoke.sh" >&2
  exit 1
fi
eval "$S01_SRC"
s01_check

if grep -q '^PASS|S-01|' "$RESULTS_FILE"; then
  echo "PASS: S-01 finds the shared stack from a throwaway worktree (COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME})"
  exit 0
fi

echo "FAIL: S-01 did not pass from the throwaway worktree" >&2
cat "$RESULTS_FILE" >&2
exit 1
