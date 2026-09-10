#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# Contract test for cleanup() in scripts/ci/docker-gate.sh. The gate builds
# with --no-cache, so every run orphans the previous run's layers; cleanup()
# removed only the run's own tags. 100 dangling images (526 GB) filled the
# shared Docker Desktop VM on 2026-09-08 and took the dev stack with it.
#
# Needs no daemon: the function is extracted from the gate and evaluated with
# `docker` shimmed to a logging stub, so the assertions are about which
# commands the gate ISSUES. Run: bash scripts/tests/test_docker_gate_prune.sh

set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GATE="$ROOT/scripts/ci/docker-gate.sh"

FAILED=0
ok()  { echo "  PASS: $1"; }
bad() { echo "  FAIL: $1"; FAILED=1; }

# The gate is a top-to-bottom script (it would run the whole build if sourced),
# so lift just the function. Brace-counted rather than "until a lone `}`", so
# it reads a one-line body and a multi-line one the same way.
CLEANUP_SRC="$(awk '
    /^cleanup\(\) \{/ { f = 1 }
    f {
        print
        n = gsub(/\{/, "{"); d += n
        n = gsub(/\}/, "}"); d -= n
        if (d <= 0) exit
    }' "$GATE")"
if [ -z "$CLEANUP_SRC" ]; then
    echo "  FAIL: could not extract cleanup() from $GATE"
    exit 1
fi

LOG=""
# Logging stub. Writes to the file, not stdout, so the callers' own
# `>/dev/null` redirections cannot swallow the record.
docker() { printf '%s\n' "$*" >> "$LOG"; }

MCP_IMG="cerid-mcp-test-42"
WEB_IMG="cerid-web-test-42"
eval "$CLEANUP_SRC"

run_cleanup() {  # $1: value for RUNNER_ENVIRONMENT ("" = unset)
    LOG="$(mktemp)"
    if [ -n "$1" ]; then
        RUNNER_ENVIRONMENT="$1" cleanup
    else
        unset RUNNER_ENVIRONMENT
        cleanup
    fi
}

echo "[test] 1. self-hosted runner → prunes dangling images"
run_cleanup "self-hosted"
grep -q "rmi -f $MCP_IMG $WEB_IMG" "$LOG" \
    && ok "the run's own tags are still removed" || bad "rmi no longer issued"
grep -q "image prune -f" "$LOG" \
    && ok "dangling images pruned on the self-hosted path" || bad "no prune on the self-hosted path"

echo "[test] 2. GitHub-hosted runner → no prune (throwaway VM, nothing to reclaim)"
run_cleanup "github-hosted"
grep -q "rmi -f $MCP_IMG $WEB_IMG" "$LOG" \
    && ok "the run's own tags are removed" || bad "rmi no longer issued"
grep -q "prune" "$LOG" \
    && bad "pruned on a hosted runner" || ok "no prune on a hosted runner"

echo "[test] 3. RUNNER_ENVIRONMENT unset (local run) → prunes"
run_cleanup ""
grep -q "image prune -f" "$LOG" \
    && ok "unset is treated as not-hosted" || bad "no prune when RUNNER_ENVIRONMENT is unset"

echo "[test] 4. the prune is never the destructive kind"
run_cleanup "self-hosted"
grep -qE "system prune|prune .*-a\b|prune .*--all" "$LOG" \
    && bad "issued a system-wide or --all prune (the daemon holds the dev stack)" \
    || ok "no system prune, no --all"

echo
if [ "$FAILED" -eq 0 ]; then echo "test_docker_gate_prune.sh: all assertions passed"; else echo "test_docker_gate_prune.sh: FAILURES"; fi
exit "$FAILED"
