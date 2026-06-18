#!/bin/bash
# Cerid AI Beta — Smoke Tests
# Validates that the Docker Compose stack is healthy and reachable.
# Must pass before any other test tier runs.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/lib/assert.sh"

export RESULTS_FILE="${SCRIPT_DIR}/reports/smoke.results"
> "$RESULTS_FILE"

MCP_BASE="http://localhost:8888"
# Read REDIS_PASSWORD from the canonical operator .env when the test
# harness shell didn't inherit it. The hardcoded `cerid-dev` fallback
# only works for fresh-clone bootstraps; live stacks use the .env
# password.
if [[ -z "${REDIS_PASSWORD:-}" && -f "${SCRIPT_DIR}/../../.env" ]]; then
  REDIS_PASSWORD=$(grep '^REDIS_PASSWORD=' "${SCRIPT_DIR}/../../.env" | head -1 | cut -d= -f2-)
fi
REDIS_PW="${REDIS_PASSWORD:-cerid-dev}"
FAILED=0

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     SMOKE TESTS (P0 — Gate)          ║"
echo "╚══════════════════════════════════════╝"
echo ""

# S-01: Docker containers running
s01_check() {
  local start end duration
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  local ps_out
  ps_out=$(docker compose ps --format '{{.Name}} {{.State}}' 2>/dev/null || echo "")
  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")

  local missing=""
  for svc in ai-companion-mcp ai-companion-neo4j ai-companion-chroma ai-companion-redis; do
    if ! echo "$ps_out" | grep -q "$svc.*running"; then
      missing="${missing} ${svc}"
    fi
  done

  if [[ -z "$missing" ]]; then
    _pass "S-01" "Docker containers running" "$duration"
  else
    _fail "S-01" "Docker containers running" "$duration" "Missing/not running:${missing}"
    FAILED=1
  fi
}
s01_check

# S-02: Health endpoint
assert_json_field "${MCP_BASE}/health" '.status' "healthy" "S-02" "Health endpoint returns healthy" || FAILED=1

# S-03: ChromaDB heartbeat — v1 was deprecated in Chroma 1.x (returns
# HTTP 410); v2 is the current endpoint shipping with chromadb==1.5.9.
assert_http_status "http://localhost:8001/api/v2/heartbeat" "200" "S-03" "ChromaDB heartbeat" || FAILED=1

# S-04: Neo4j browser
assert_http_status "http://localhost:7474" "200" "S-04" "Neo4j HTTP reachable" || FAILED=1

# S-05: Redis ping
assert_command_output "docker exec ai-companion-redis redis-cli -a ${REDIS_PW} ping 2>/dev/null" "PONG" "S-05" "Redis ping" || FAILED=1

# S-06: Frontend reachable
assert_http_status "http://localhost:3000" "200" "S-06" "Frontend reachable" || FAILED=1

# S-07: Collections endpoint
assert_json_exists "${MCP_BASE}/collections" '.total' "S-07" "Collections endpoint" || FAILED=1

# S-08: validate-env.sh --quick
if [[ -f "${SCRIPT_DIR}/../../scripts/validate-env.sh" ]]; then
  assert_command "cd ${SCRIPT_DIR}/../.. && bash scripts/validate-env.sh --quick" "0" "S-08" "validate-env.sh --quick" || FAILED=1
else
  _skip "S-08" "validate-env.sh --quick" "Script not found"
fi

echo ""
echo "Smoke results: $(grep -c '^PASS|' "$RESULTS_FILE") passed, $(grep -c '^FAIL|' "$RESULTS_FILE" 2>/dev/null || echo 0) failed"
exit $FAILED
