#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Contract test for detect_conflicts() in scripts/lib/healthcheck.sh — the
# cross-project/dir squatter guard added after the 2026-06-06 install failure
# (public-mirror compose run from its own dir held the personal container
# names while running; the personal rebuild collided with a raw daemon error).
#
# Requires docker. Self-contained: spins a labelled throwaway container to
# simulate a foreign-project holder, asserts detect/abort/reclaim behaviour,
# and always cleans up. Run: bash scripts/tests/test_detect_conflicts.sh

set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export CERID_ENV_FILE="$ROOT/.env"
# shellcheck source=../lib/healthcheck.sh
source "$ROOT/scripts/lib/healthcheck.sh"

NAME="cerid-dctest-$$"
FAILED=0
ok()   { echo "  PASS: $1"; }
bad()  { echo "  FAIL: $1"; FAILED=1; }
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

mk_foreign() {  # a container labelled as a DIFFERENT compose project + dir
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run -d --name "$NAME" \
        --label com.docker.compose.project=some-other-project \
        --label com.docker.compose.project.working_dir=/somewhere/else \
        alpine:3.21 sleep 600 >/dev/null
}

echo "[test] 1. no holder → returns 0"
detect_conflicts "cerid-ai-internal" "$ROOT" "$NAME" 2>/dev/null \
    && ok "absent name is not a conflict" || bad "absent name flagged"

echo "[test] 2. foreign holder, no reclaim, non-interactive → aborts (1), container preserved"
mk_foreign
( CERID_RECLAIM="" detect_conflicts "cerid-ai-internal" "$ROOT" "$NAME" </dev/null ) >/dev/null 2>&1
[ $? -ne 0 ] && ok "foreign holder causes abort" || bad "foreign holder not flagged"
docker inspect "$NAME" >/dev/null 2>&1 && ok "foreign container preserved (not auto-destroyed)" \
    || bad "foreign container was destroyed without consent"

echo "[test] 3. foreign holder, CERID_RECLAIM=true → reclaims (0), container removed"
CERID_RECLAIM=true detect_conflicts "cerid-ai-internal" "$ROOT" "$NAME" >/dev/null 2>&1 \
    && ok "reclaim returns success" || bad "reclaim did not succeed"
docker inspect "$NAME" >/dev/null 2>&1 && bad "container still present after reclaim" \
    || ok "foreign container removed on reclaim"

echo "[test] 4. holder labelled as OURS (same project+dir) → not a conflict"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" \
    --label com.docker.compose.project=cerid-ai-internal \
    --label com.docker.compose.project.working_dir="$ROOT" \
    alpine:3.21 sleep 600 >/dev/null
detect_conflicts "cerid-ai-internal" "$ROOT" "$NAME" >/dev/null 2>&1 \
    && ok "same project+dir is ours, not foreign" || bad "ours flagged as foreign"
docker inspect "$NAME" >/dev/null 2>&1 && ok "ours container left untouched" || bad "ours container removed"

echo ""
[ "$FAILED" -eq 0 ] && echo "ALL PASS" || { echo "FAILURES PRESENT"; exit 1; }
