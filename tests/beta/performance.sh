#!/bin/bash
# Cerid AI Beta — Performance Tests
# Curl-based latency benchmarks against localhost:8888 (MCP) and localhost:3000 (GUI).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/lib/assert.sh"

export RESULTS_FILE="${SCRIPT_DIR}/reports/performance.results"
> "$RESULTS_FILE"

MCP_BASE="http://localhost:8888"
GUI_BASE="http://localhost:3000"
FAILED=0

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   PERFORMANCE TESTS (Latency)        ║"
echo "╚══════════════════════════════════════╝"
echo ""

# --- Percentile helpers ---

# Collect timings into a temp file, sort, and compute percentiles.
# Usage: compute_percentiles <sorted_file> <count>
# Sets global vars: P50 P95 P99
compute_percentiles() {
  local file="$1" count="$2"
  local idx50 idx95 idx99
  idx50=$(awk "BEGIN{printf \"%d\", ($count * 50 / 100) + 0.5}")
  idx95=$(awk "BEGIN{printf \"%d\", ($count * 95 / 100) + 0.5}")
  idx99=$(awk "BEGIN{printf \"%d\", ($count * 99 / 100) + 0.5}")
  # Clamp to at least 1
  [[ "$idx50" -lt 1 ]] && idx50=1
  [[ "$idx95" -lt 1 ]] && idx95=1
  [[ "$idx99" -lt 1 ]] && idx99=1
  # Clamp to at most count
  [[ "$idx50" -gt "$count" ]] && idx50="$count"
  [[ "$idx95" -gt "$count" ]] && idx95="$count"
  [[ "$idx99" -gt "$count" ]] && idx99="$count"
  P50=$(sed -n "${idx50}p" "$file")
  P95=$(sed -n "${idx95}p" "$file")
  P99=$(sed -n "${idx99}p" "$file")
}

# --- P-01: Health endpoint latency ---
p01_check() {
  local tmpfile start end duration
  tmpfile=$(mktemp)
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")

  for i in $(seq 1 50); do
    curl -w '%{time_total}\n' -s -o /dev/null --connect-timeout 5 --max-time 10 "${MCP_BASE}/health" >> "$tmpfile" 2>/dev/null || echo "9999" >> "$tmpfile"
  done

  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")

  sort -n "$tmpfile" -o "$tmpfile"
  compute_percentiles "$tmpfile" 50

  local notes="p50=${P50}s p95=${P95}s p99=${P99}s"
  # Pass if p95 < 0.5s
  local pass
  pass=$(awk "BEGIN{print ($P95 < 0.5) ? 1 : 0}")
  if [[ "$pass" == "1" ]]; then
    _pass "P-01" "Health endpoint latency (p95 < 500ms)" "$duration" "$notes"
  else
    _fail "P-01" "Health endpoint latency (p95 < 500ms)" "$duration" "$notes"
    FAILED=1
  fi
  rm -f "$tmpfile"
}
p01_check

# --- P-02: Query latency ---
p02_check() {
  local tmpfile start end duration
  tmpfile=$(mktemp)
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")

  for i in $(seq 1 10); do
    curl -w '%{time_total}\n' -s -o /dev/null --connect-timeout 5 --max-time 30 \
      -X POST -H 'Content-Type: application/json' \
      -d '{"query":"test","top_k":3}' \
      "${MCP_BASE}/agent/query" >> "$tmpfile" 2>/dev/null || echo "9999" >> "$tmpfile"
  done

  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")

  sort -n "$tmpfile" -o "$tmpfile"
  compute_percentiles "$tmpfile" 10

  local notes="p50=${P50}s p95=${P95}s p99=${P99}s"
  # Pass if p95 < 15s
  local pass
  pass=$(awk "BEGIN{print ($P95 < 15) ? 1 : 0}")
  if [[ "$pass" == "1" ]]; then
    _pass "P-02" "Query latency (p95 < 15s)" "$duration" "$notes"
  else
    _fail "P-02" "Query latency (p95 < 15s)" "$duration" "$notes"
    FAILED=1
  fi
  rm -f "$tmpfile"
}
p02_check

# --- P-03: Artifact listing ---
p03_check() {
  local tmpfile start end duration
  tmpfile=$(mktemp)
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")

  for i in $(seq 1 20); do
    curl -w '%{time_total}\n' -s -o /dev/null --connect-timeout 5 --max-time 10 \
      "${MCP_BASE}/artifacts?limit=50" >> "$tmpfile" 2>/dev/null || echo "9999" >> "$tmpfile"
  done

  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")

  sort -n "$tmpfile" -o "$tmpfile"
  compute_percentiles "$tmpfile" 20

  local notes="p50=${P50}s p95=${P95}s p99=${P99}s"
  # Pass if p95 < 2s
  local pass
  pass=$(awk "BEGIN{print ($P95 < 2) ? 1 : 0}")
  if [[ "$pass" == "1" ]]; then
    _pass "P-03" "Artifact listing (p95 < 2s)" "$duration" "$notes"
  else
    _fail "P-03" "Artifact listing (p95 < 2s)" "$duration" "$notes"
    FAILED=1
  fi
  rm -f "$tmpfile"
}
p03_check

# --- P-04: Concurrent queries ---
p04_check() {
  local start end duration
  local tmpdir
  tmpdir=$(mktemp -d)
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")

  # Launch 5 parallel POST /agent/query requests (use trading-agent for 80/min limit)
  for i in $(seq 1 5); do
    curl -s -o /dev/null -w '%{http_code}' --connect-timeout 10 --max-time 30 \
      -X POST -H 'Content-Type: application/json' -H 'X-Client-ID: trading-agent' \
      -d '{"query":"test","top_k":3}' \
      "${MCP_BASE}/agent/query" > "${tmpdir}/result_${i}" 2>/dev/null &
  done
  wait

  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")

  # Check all returned 200
  local all_ok=1
  local codes=""
  for i in $(seq 1 5); do
    local code
    code=$(cat "${tmpdir}/result_${i}" 2>/dev/null || echo "000")
    codes="${codes} ${code}"
    if [[ "$code" != "200" ]]; then
      all_ok=0
    fi
  done

  local notes="status_codes:${codes}"
  if [[ "$all_ok" == "1" ]]; then
    _pass "P-04" "Concurrent queries (5 parallel, all 200)" "$duration" "$notes"
  else
    _fail "P-04" "Concurrent queries (5 parallel, all 200)" "$duration" "$notes"
    FAILED=1
  fi
  rm -rf "$tmpdir"
}
p04_check

echo ""
echo "Performance results: $(grep -c '^PASS|' "$RESULTS_FILE") passed, $(grep -c '^FAIL|' "$RESULTS_FILE" 2>/dev/null || echo 0) failed"
exit $FAILED
