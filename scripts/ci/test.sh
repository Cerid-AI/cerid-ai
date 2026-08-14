#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# The `test` job's work, extracted so the Linux-native path and the
# containerised macOS path run ONE definition instead of two copies that drift.
#
# Writes src/mcp/coverage.xml into the working tree; the Codecov upload stays a
# host-side workflow step, reading that file out of the mounted repo.
#
# Run from the repo root.
set -euo pipefail

pip install -r src/mcp/requirements.txt
pip install pytest pytest-asyncio httpx pytest-cov respx 'fakeredis>=2.0,<3'

(
  cd src/mcp
  python -m pytest tests/ -m "not benchmark_slo and not integration" \
    -v --tb=short --cov=. --cov-report=term-missing \
    --cov-report=xml:coverage.xml --cov-fail-under=20
)

python -m pytest scripts/tests/ -q

# detect_conflicts() contract test — a bash script, so pytest's collection
# above never touches it (RA-69). Spins throwaway docker containers, so it
# only runs where a docker daemon is actually reachable (the containerised
# macOS CI path runs this script inside python:3.12 without the docker
# socket mounted; skip there rather than fail on an unrelated gap).
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  bash scripts/tests/test_detect_conflicts.sh
else
  echo "scripts/ci/test.sh: docker unavailable — skipping scripts/tests/test_detect_conflicts.sh" >&2
fi
