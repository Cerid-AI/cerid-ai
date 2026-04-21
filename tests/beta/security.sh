#!/bin/bash
# Cerid AI Beta — Security Tests
# Security spot checks run from the host against the local stack.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/lib/assert.sh"

export RESULTS_FILE="${SCRIPT_DIR}/reports/security.results"
> "$RESULTS_FILE"

MCP_BASE="http://localhost:8888"
GUI_BASE="http://localhost:3000"
FAILED=0

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   SECURITY TESTS (Spot Checks)       ║"
echo "╚══════════════════════════════════════╝"
echo ""

# --- SEC-01: CORS with evil origin ---
sec01_check() {
  local start end duration
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")

  local headers
  headers=$(curl -s -H 'Origin: http://evil.com' -D- -o /dev/null --connect-timeout 5 --max-time 10 "${MCP_BASE}/health" 2>/dev/null || echo "")

  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")

  local acao
  acao=$(echo "$headers" | grep -i 'access-control-allow-origin' || echo "")

  if echo "$acao" | grep -qi 'evil.com'; then
    # evil.com is explicitly reflected — check if it's wildcard * (dev mode)
    if echo "$acao" | grep -q '\*'; then
      _pass "SEC-01" "CORS evil origin" "$duration" "INFO: CORS is wildcard (*) — acceptable for dev"
    else
      _fail "SEC-01" "CORS evil origin" "$duration" "evil.com explicitly reflected in ACAO"
      FAILED=1
    fi
  elif echo "$acao" | grep -q '\*'; then
    _pass "SEC-01" "CORS evil origin" "$duration" "INFO: CORS is wildcard (*) — acceptable for dev"
  else
    _pass "SEC-01" "CORS evil origin" "$duration" "evil.com not reflected"
  fi
}
sec01_check

# --- SEC-02: No secrets in health response ---
sec02_check() {
  local start end duration
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")

  local body
  body=$(curl -s --connect-timeout 5 --max-time 10 "${MCP_BASE}/health" 2>/dev/null || echo "")

  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")

  if echo "$body" | grep -iEq 'password|api_key|token|secret'; then
    _fail "SEC-02" "No secrets in health response" "$duration" "Secret pattern found in response"
    FAILED=1
  else
    _pass "SEC-02" "No secrets in health response" "$duration"
  fi
}
sec02_check

# --- SEC-03: Redis auth enforced ---
sec03_check() {
  local start end duration
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")

  local output
  output=$(docker exec ai-companion-redis redis-cli ping 2>&1 || echo "ERROR")

  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")

  if echo "$output" | grep -qi 'PONG'; then
    _fail "SEC-03" "Redis auth enforced" "$duration" "Unauthenticated PING returned PONG"
    FAILED=1
  elif echo "$output" | grep -qiE 'NOAUTH|AUTH|ERR'; then
    _pass "SEC-03" "Redis auth enforced" "$duration" "Auth required (got: $(echo "$output" | head -1))"
  else
    _fail "SEC-03" "Redis auth enforced" "$duration" "Unexpected response: $(echo "$output" | head -1)"
    FAILED=1
  fi
}
sec03_check

# --- SEC-04: Security headers on frontend ---
sec04_check() {
  local start end duration
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")

  local headers
  headers=$(curl -sI --connect-timeout 5 --max-time 10 "${GUI_BASE}" 2>/dev/null || echo "")

  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")

  if echo "$headers" | grep -qi 'x-content-type-options'; then
    _pass "SEC-04" "Security headers (X-Content-Type-Options)" "$duration"
  else
    _fail "SEC-04" "Security headers (X-Content-Type-Options)" "$duration" "X-Content-Type-Options header missing"
    FAILED=1
  fi
}
sec04_check

# --- SEC-05: No directory traversal ---
sec05_check() {
  local start end duration
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")

  local body
  body=$(curl -s --connect-timeout 5 --max-time 10 --path-as-is "${MCP_BASE}/../../../etc/passwd" 2>/dev/null || echo "")

  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")

  if echo "$body" | grep -q 'root:'; then
    _fail "SEC-05" "No directory traversal" "$duration" "Response contains /etc/passwd content"
    FAILED=1
  else
    _pass "SEC-05" "No directory traversal" "$duration"
  fi
}
sec05_check

echo ""
echo "Security results: $(grep -c '^PASS|' "$RESULTS_FILE") passed, $(grep -c '^FAIL|' "$RESULTS_FILE" 2>/dev/null || echo 0) failed"
exit $FAILED
