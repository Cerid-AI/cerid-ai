#!/bin/bash
# Beta test assertion helpers — curl-based HTTP and command assertions
# Source this file in test scripts: source "$(dirname "$0")/lib/assert.sh"

set -euo pipefail

# Results file — set by caller or default
RESULTS_FILE="${RESULTS_FILE:-/tmp/beta-test-results.txt}"

_pass() {
  local id="$1" name="$2" duration="$3" notes="${4:-}"
  echo "PASS|${id}|${name}|${duration}s|${notes}" >> "$RESULTS_FILE"
  printf "  ✓ %-8s %s (%ss)\n" "$id" "$name" "$duration"
}

_fail() {
  local id="$1" name="$2" duration="$3" notes="$4"
  echo "FAIL|${id}|${name}|${duration}s|${notes}" >> "$RESULTS_FILE"
  printf "  ✗ %-8s %s (%ss) — %s\n" "$id" "$name" "$duration" "$notes" >&2
}

_skip() {
  local id="$1" name="$2" notes="${3:-skipped}"
  echo "SKIP|${id}|${name}|0s|${notes}" >> "$RESULTS_FILE"
  printf "  ⊘ %-8s %s — %s\n" "$id" "$name" "$notes"
}

# assert_http_status URL EXPECTED_CODE TEST_ID TEST_NAME [EXTRA_CURL_ARGS...]
assert_http_status() {
  local url="$1" expected="$2" id="$3" name="$4"
  shift 4
  local start end duration code
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 "$@" "$url" 2>/dev/null || echo "000")
  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")
  if [[ "$code" == "$expected" ]]; then
    _pass "$id" "$name" "$duration"
    return 0
  else
    _fail "$id" "$name" "$duration" "Expected HTTP $expected, got $code"
    return 1
  fi
}

# assert_json_field URL JQ_EXPR EXPECTED_VALUE TEST_ID TEST_NAME [EXTRA_CURL_ARGS...]
# JQ_EXPR should return a string value; EXPECTED_VALUE is compared as string
assert_json_field() {
  local url="$1" jq_expr="$2" expected="$3" id="$4" name="$5"
  shift 5
  local start end duration body actual code
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  body=$(curl -s --connect-timeout 5 --max-time 10 -w '\n%{http_code}' "$@" "$url" 2>/dev/null || echo -e "\n000")
  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")
  code=$(echo "$body" | tail -1)
  body=$(echo "$body" | sed '$d')
  if [[ "$code" != "200" ]]; then
    _fail "$id" "$name" "$duration" "HTTP $code (expected 200)"
    return 1
  fi
  actual=$(echo "$body" | jq -r "$jq_expr" 2>/dev/null || echo "JQ_ERROR")
  if [[ "$actual" == "$expected" ]]; then
    _pass "$id" "$name" "$duration"
    return 0
  else
    _fail "$id" "$name" "$duration" "Expected '$expected', got '$actual'"
    return 1
  fi
}

# assert_json_exists URL JQ_EXPR TEST_ID TEST_NAME
# Passes if jq_expr returns a non-null, non-empty value
assert_json_exists() {
  local url="$1" jq_expr="$2" id="$3" name="$4"
  shift 4
  local start end duration body actual code
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  body=$(curl -s --connect-timeout 5 --max-time 10 -w '\n%{http_code}' "$@" "$url" 2>/dev/null || echo -e "\n000")
  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")
  code=$(echo "$body" | tail -1)
  body=$(echo "$body" | sed '$d')
  if [[ "$code" != "200" ]]; then
    _fail "$id" "$name" "$duration" "HTTP $code (expected 200)"
    return 1
  fi
  actual=$(echo "$body" | jq -r "$jq_expr" 2>/dev/null || echo "")
  if [[ -n "$actual" && "$actual" != "null" ]]; then
    _pass "$id" "$name" "$duration"
    return 0
  else
    _fail "$id" "$name" "$duration" "Field '$jq_expr' is null or missing"
    return 1
  fi
}

# assert_command "COMMAND" EXPECTED_EXIT TEST_ID TEST_NAME
assert_command() {
  local cmd="$1" expected_exit="$2" id="$3" name="$4"
  local start end duration actual_exit
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  eval "$cmd" > /dev/null 2>&1
  actual_exit=$?
  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")
  if [[ "$actual_exit" == "$expected_exit" ]]; then
    _pass "$id" "$name" "$duration"
    return 0
  else
    _fail "$id" "$name" "$duration" "Exit code $actual_exit (expected $expected_exit)"
    return 1
  fi
}

# assert_command_output "COMMAND" EXPECTED_SUBSTRING TEST_ID TEST_NAME
assert_command_output() {
  local cmd="$1" expected="$2" id="$3" name="$4"
  local start end duration output
  start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  output=$(eval "$cmd" 2>&1) || true
  end=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  duration=$(awk "BEGIN{printf \"%.2f\", ($end - $start)/1000000000}")
  if echo "$output" | grep -q "$expected"; then
    _pass "$id" "$name" "$duration"
    return 0
  else
    _fail "$id" "$name" "$duration" "Output missing '$expected'"
    return 1
  fi
}

# Count results from the results file
count_results() {
  local status="${1:-}"
  if [[ -z "$status" ]]; then
    wc -l < "$RESULTS_FILE" | tr -d ' '
  else
    grep -c "^${status}|" "$RESULTS_FILE" 2>/dev/null || echo "0"
  fi
}
