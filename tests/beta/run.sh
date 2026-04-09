#!/bin/bash
# Cerid AI Beta Test Harness — Main Orchestrator
#
# Usage:
#   ./tests/beta/run.sh                    # Run all tiers
#   ./tests/beta/run.sh --smoke            # Smoke only
#   ./tests/beta/run.sh --functional       # Smoke + Functional
#   ./tests/beta/run.sh --skip-browser     # Skip Playwright E2E
#   ./tests/beta/run.sh --auth             # Include multi-user auth tests
#   ./tests/beta/run.sh --skip-performance # Skip performance benchmarks
#   ./tests/beta/run.sh --eval             # Evaluation & efficacy suite only
#   ./tests/beta/run.sh --full             # All tiers + evaluation suite
#   ./tests/beta/run.sh --eval --browser   # Eval + browser E2E
#
# Exit codes: 0 = all P0 pass, 1 = P0 failure, 2 = run error

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${SCRIPT_DIR}/lib/report.sh"

# Parse flags
RUN_SMOKE=true
RUN_FUNCTIONAL=true
RUN_INTEGRATION=true
RUN_PERFORMANCE=true
RUN_SECURITY=true
RUN_BROWSER=true
RUN_AUTH=false
RUN_EVAL=false
STOP_AFTER=""

for arg in "$@"; do
  case "$arg" in
    --smoke) STOP_AFTER="smoke" ;;
    --functional) STOP_AFTER="functional" ;;
    --skip-browser) RUN_BROWSER=false ;;
    --skip-performance) RUN_PERFORMANCE=false ;;
    --auth) RUN_AUTH=true ;;
    --eval) RUN_EVAL=true; RUN_SMOKE=false; RUN_FUNCTIONAL=false; RUN_INTEGRATION=false; RUN_PERFORMANCE=false; RUN_SECURITY=false; RUN_BROWSER=false ;;
    --full) RUN_EVAL=true ;;
    --browser) RUN_BROWSER=true ;;
    --help|-h)
      echo "Usage: $0 [--smoke|--functional|--skip-browser|--skip-performance|--auth|--eval|--full|--browser]"
      exit 0
      ;;
  esac
done

# Ensure reports directory exists
mkdir -p "${SCRIPT_DIR}/reports"

# Clean old results files
rm -f "${SCRIPT_DIR}/reports/"*.results

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   CERID AI — BETA TEST HARNESS v1.0.0       ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# Initialize report (report_init runs in subshell via $(), so re-set globals)
REPORT=$(report_init)
REPORT_FILE="$REPORT"
REPORT_DIR="$(dirname "$REPORT_FILE")"
echo "Report: ${REPORT}"
echo ""

OVERALL_EXIT=0

# ─────────────────────────────────────────────────
# TIER 1: SMOKE TESTS
# ─────────────────────────────────────────────────
if $RUN_SMOKE; then
  cd "$REPO_ROOT"
  bash "${SCRIPT_DIR}/smoke.sh"
  SMOKE_EXIT=$?

  report_section "Smoke Tests (P0)"
  report_append_results "${SCRIPT_DIR}/reports/smoke.results"

  # Log issues for failures
  while IFS='|' read -r status id name duration notes; do
    if [[ "$status" == "FAIL" ]]; then
      report_issue "critical" "$name failed" "infrastructure" "$id" "infra" \
        "Run smoke test $id" "PASS" "$notes"
    fi
  done < "${SCRIPT_DIR}/reports/smoke.results"

  if [[ $SMOKE_EXIT -ne 0 ]]; then
    echo ""
    echo "⛔ Smoke tests FAILED — aborting remaining tiers."
    report_text "\n> **Smoke tests failed. Remaining tiers were not executed.**\n"
    report_finalize
    exit 1
  fi

  [[ "$STOP_AFTER" == "smoke" ]] && { report_finalize; exit 0; }
fi

# ─────────────────────────────────────────────────
# TIER 2: FUNCTIONAL API TESTS
# ─────────────────────────────────────────────────
if $RUN_FUNCTIONAL; then
  echo ""
  echo "╔══════════════════════════════════════╗"
  echo "║   FUNCTIONAL API TESTS               ║"
  echo "╚══════════════════════════════════════╝"
  echo ""

  # Determine Docker network name
  DOCKER_NETWORK=$(docker network ls --format '{{.Name}}' | grep 'llm-network' | head -1)
  if [[ -z "$DOCKER_NETWORK" ]]; then
    echo "⚠️  llm-network not found, trying cerid-ai_llm-network"
    DOCKER_NETWORK="cerid-ai_llm-network"
  fi

  # Run functional tests in Docker
  FUNC_AUTH_FLAG=""
  if $RUN_AUTH; then
    FUNC_AUTH_FLAG="functional_auth.py"
  fi

  docker run --rm --network "$DOCKER_NETWORK" \
    -v "${SCRIPT_DIR}:/tests" -w /tests \
    python:3.11-slim bash -c "
      pip install -q httpx pytest 2>/dev/null
      python -m pytest functional.py ${FUNC_AUTH_FLAG} -v --tb=short \
        --junitxml=reports/functional.xml 2>&1
    "
  FUNC_EXIT=$?

  # Convert junit XML to results format if possible
  report_section "Functional API Tests"
  if [[ -f "${SCRIPT_DIR}/reports/functional.xml" ]]; then
    # Parse junit XML for pass/fail counts
    python3 -c "
import xml.etree.ElementTree as ET
import sys
tree = ET.parse('${SCRIPT_DIR}/reports/functional.xml')
root = tree.getroot()
for suite in root.iter('testsuite'):
    pass
for tc in root.iter('testcase'):
    name = tc.get('name', 'unknown')
    time_val = tc.get('time', '0')
    failure = tc.find('failure')
    skip = tc.find('skipped')
    tid = name.replace('test_', '').upper().replace('_', '-', 1).split('-')[0] + '-' + name.replace('test_', '').upper().replace('_', '-', 1).split('-')[1] if '-' in name.replace('test_', '').upper().replace('_', '-', 1) else name
    if failure is not None:
        msg = (failure.get('message', '') or '')[:80]
        print(f'FAIL|{tid}|{name}|{time_val}s|{msg}')
    elif skip is not None:
        print(f'SKIP|{tid}|{name}|{time_val}s|skipped')
    else:
        print(f'PASS|{tid}|{name}|{time_val}s|')
" > "${SCRIPT_DIR}/reports/functional.results" 2>/dev/null || true
    report_append_results "${SCRIPT_DIR}/reports/functional.results"
  fi

  # Log functional failures as issues
  if [[ -f "${SCRIPT_DIR}/reports/functional.results" ]]; then
    while IFS='|' read -r status id name duration notes; do
      if [[ "$status" == "FAIL" ]]; then
        # Determine severity by test ID prefix
        severity="high"
        echo "$name" | grep -qE "f0[0-9]|f1[0-9]" && severity="critical"
        report_issue "$severity" "$name failed" "functionality" "$id" "backend" \
          "Run functional test $name" "PASS" "${notes:-Test failed}"
      fi
    done < "${SCRIPT_DIR}/reports/functional.results"
  fi

  if [[ $FUNC_EXIT -ne 0 ]]; then
    OVERALL_EXIT=1
    echo ""
    echo "⚠️  Some functional tests failed."
  fi

  [[ "$STOP_AFTER" == "functional" ]] && { report_finalize; exit $OVERALL_EXIT; }
fi

# ─────────────────────────────────────────────────
# TIER 3: INTEGRATION TESTS
# ─────────────────────────────────────────────────
if $RUN_INTEGRATION; then
  echo ""
  echo "╔══════════════════════════════════════╗"
  echo "║   INTEGRATION TESTS                  ║"
  echo "╚══════════════════════════════════════╝"
  echo ""

  DOCKER_NETWORK=$(docker network ls --format '{{.Name}}' | grep 'llm-network' | head -1)
  [[ -z "$DOCKER_NETWORK" ]] && DOCKER_NETWORK="cerid-ai_llm-network"

  docker run --rm --network "$DOCKER_NETWORK" \
    -v "${SCRIPT_DIR}:/tests" -w /tests \
    python:3.11-slim bash -c "
      pip install -q httpx pytest 2>/dev/null
      python -m pytest integration.py -v --tb=short \
        --junitxml=reports/integration.xml 2>&1
    "
  INT_EXIT=$?

  report_section "Integration Tests"
  if [[ -f "${SCRIPT_DIR}/reports/integration.xml" ]]; then
    python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('${SCRIPT_DIR}/reports/integration.xml')
root = tree.getroot()
for tc in root.iter('testcase'):
    name = tc.get('name', 'unknown')
    time_val = tc.get('time', '0')
    failure = tc.find('failure')
    skip = tc.find('skipped')
    tid = name.replace('test_', '').upper().replace('_', '-', 1).split('-')[0] + '-' + name.replace('test_', '').upper().replace('_', '-', 1).split('-')[1] if '-' in name.replace('test_', '').upper().replace('_', '-', 1) else name
    if failure is not None:
        msg = (failure.get('message', '') or '')[:80]
        print(f'FAIL|{tid}|{name}|{time_val}s|{msg}')
    elif skip is not None:
        print(f'SKIP|{tid}|{name}|{time_val}s|skipped')
    else:
        print(f'PASS|{tid}|{name}|{time_val}s|')
" > "${SCRIPT_DIR}/reports/integration.results" 2>/dev/null || true
    report_append_results "${SCRIPT_DIR}/reports/integration.results"
  fi

  if [[ -f "${SCRIPT_DIR}/reports/integration.results" ]]; then
    while IFS='|' read -r status id name duration notes; do
      if [[ "$status" == "FAIL" ]]; then
        report_issue "high" "$name failed" "integration" "$id" "integration" \
          "Run integration test $name" "PASS" "${notes:-Test failed}"
      fi
    done < "${SCRIPT_DIR}/reports/integration.results"
  fi

  [[ $INT_EXIT -ne 0 ]] && OVERALL_EXIT=1
fi

# ─────────────────────────────────────────────────
# TIER 4: PERFORMANCE TESTS
# ─────────────────────────────────────────────────
if $RUN_PERFORMANCE; then
  echo ""
  cd "$REPO_ROOT"
  bash "${SCRIPT_DIR}/performance.sh"
  PERF_EXIT=$?

  report_section "Performance Tests"
  [[ -f "${SCRIPT_DIR}/reports/performance.results" ]] && \
    report_append_results "${SCRIPT_DIR}/reports/performance.results"

  if [[ -f "${SCRIPT_DIR}/reports/performance.results" ]]; then
    while IFS='|' read -r status id name duration notes; do
      if [[ "$status" == "FAIL" ]]; then
        report_issue "medium" "$name exceeded threshold" "performance" "$id" "backend" \
          "Run performance benchmark $id" "Within threshold" "${notes:-Exceeded latency threshold}"
      fi
    done < "${SCRIPT_DIR}/reports/performance.results"
  fi
fi

# ─────────────────────────────────────────────────
# TIER 5: SECURITY TESTS
# ─────────────────────────────────────────────────
if $RUN_SECURITY; then
  echo ""
  cd "$REPO_ROOT"
  bash "${SCRIPT_DIR}/security.sh"
  SEC_EXIT=$?

  report_section "Security Tests"
  [[ -f "${SCRIPT_DIR}/reports/security.results" ]] && \
    report_append_results "${SCRIPT_DIR}/reports/security.results"

  if [[ -f "${SCRIPT_DIR}/reports/security.results" ]]; then
    while IFS='|' read -r status id name duration notes; do
      if [[ "$status" == "FAIL" ]]; then
        report_issue "high" "$name" "security" "$id" "infra" \
          "Run security check $id" "Secure" "${notes:-Security check failed}"
      fi
    done < "${SCRIPT_DIR}/reports/security.results"
  fi
fi

# ─────────────────────────────────────────────────
# TIER 6: BROWSER E2E (placeholder — run interactively via Playwright MCP)
# ─────────────────────────────────────────────────
if $RUN_BROWSER; then
  report_section "Browser E2E Tests"
  report_text "\n> Browser E2E tests are run interactively via Playwright MCP tools.\n> See the test plan for E-01 through E-10 test cases.\n"
fi

# ─────────────────────────────────────────────────
# TIER 7: EVALUATION & EFFICACY SUITE
# ─────────────────────────────────────────────────
if ${RUN_EVAL:-false}; then
  echo ""
  echo "╔══════════════════════════════════════╗"
  echo "║   EVALUATION & EFFICACY SUITE        ║"
  echo "╚══════════════════════════════════════╝"
  echo ""

  DOCKER_NETWORK=$(docker network ls --format '{{.Name}}' | grep 'llm-network' | head -1)
  [[ -z "$DOCKER_NETWORK" ]] && DOCKER_NETWORK="cerid-ai_llm-network"

  mkdir -p "${SCRIPT_DIR}/eval/reports"

  docker run --rm --network "$DOCKER_NETWORK" \
    -v "${SCRIPT_DIR}:/tests" -w /tests \
    python:3.11-slim bash -c "
      pip install -q httpx pytest 'pytest-asyncio>=0.23' 2>/dev/null
      cd eval && python -m pytest . -v --tb=short \
        --junitxml=reports/eval.xml 2>&1
    "
  EVAL_EXIT=$?

  report_section "Evaluation & Efficacy Suite"
  if [[ -f "${SCRIPT_DIR}/eval/reports/eval.xml" ]]; then
    python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('${SCRIPT_DIR}/eval/reports/eval.xml')
root = tree.getroot()
for tc in root.iter('testcase'):
    name = tc.get('name', 'unknown')
    time_val = tc.get('time', '0')
    failure = tc.find('failure')
    skip = tc.find('skipped')
    if failure is not None:
        msg = (failure.get('message', '') or '')[:80]
        print(f'FAIL|EVAL|{name}|{time_val}s|{msg}')
    elif skip is not None:
        print(f'SKIP|EVAL|{name}|{time_val}s|skipped')
    else:
        print(f'PASS|EVAL|{name}|{time_val}s|')
" > "${SCRIPT_DIR}/eval/reports/eval.results" 2>/dev/null || true
    report_append_results "${SCRIPT_DIR}/eval/reports/eval.results"
  fi

  # Generate structured markdown report
  EVAL_REPORT="${SCRIPT_DIR}/eval/reports/eval-report-$(date +%Y%m%d-%H%M%S).md"
  python3 -c "
import xml.etree.ElementTree as ET, sys, os
from datetime import datetime

xml_path = '${SCRIPT_DIR}/eval/reports/eval.xml'
if not os.path.exists(xml_path):
    sys.exit(0)
tree = ET.parse(xml_path)
root = tree.getroot()

ts = root.get('timestamp', datetime.now().isoformat())
total = int(root.get('tests', 0))
failures = int(root.get('failures', 0))
errors = int(root.get('errors', 0))
skipped = int(root.get('skips', root.get('skip', '0')))
passed = total - failures - errors - skipped

tiers = {'verification_efficacy': [], 'rag_benchmark': [], 'routing_validation': []}
for tc in root.iter('testcase'):
    name = tc.get('name', '')
    classname = tc.get('classname', '')
    time_val = tc.get('time', '0')
    fail = tc.find('failure')
    skip = tc.find('skipped')
    status = 'FAIL' if fail is not None else ('SKIP' if skip is not None else 'PASS')
    detail = (fail.get('message','') if fail is not None else '')[:120]
    matched = False
    for tier_key in tiers:
        if tier_key in classname:
            tiers[tier_key].append((name, status, time_val, detail))
            matched = True
            break
    if not matched:
        tiers.setdefault('other', []).append((name, status, time_val, detail))

verdict = 'GO' if failures == 0 and errors == 0 else ('GO WITH CAVEATS' if failures <= 3 else 'NO-GO')
lines = ['# Evaluation & Efficacy Report', '', '**Date:** ' + ts, '**Verdict:** ' + verdict, '',
         '## Summary', '', '| Metric | Value |', '|--------|-------|',
         '| Total | ' + str(total) + ' |', '| Passed | ' + str(passed) + ' |',
         '| Failed | ' + str(failures) + ' |', '| Errors | ' + str(errors) + ' |',
         '| Skipped | ' + str(skipped) + ' |', '']
for tier_name, results in tiers.items():
    if not results:
        continue
    lines.append('## ' + tier_name.replace('_', ' ').title())
    lines.append('')
    lines.append('| Test | Status | Duration | Notes |')
    lines.append('|------|--------|----------|-------|')
    for n, s, t, d in results:
        emoji = 'PASS' if s == 'PASS' else ('SKIP' if s == 'SKIP' else 'FAIL')
        lines.append('| ' + n + ' | ' + emoji + ' | ' + t + 's | ' + d + ' |')
    lines.append('')
with open('${SCRIPT_DIR}/eval/reports/eval-report-latest.md', 'w') as f:
    f.write('\n'.join(lines))
print('Report: eval/reports/eval-report-latest.md')
" 2>/dev/null || true
  [[ -f "${SCRIPT_DIR}/eval/reports/eval-report-latest.md" ]] && \
    cp "${SCRIPT_DIR}/eval/reports/eval-report-latest.md" "$EVAL_REPORT"

  [[ $EVAL_EXIT -ne 0 ]] && OVERALL_EXIT=1
fi

# ─────────────────────────────────────────────────
# FINALIZE
# ─────────────────────────────────────────────────
echo ""
report_finalize

exit $OVERALL_EXIT
