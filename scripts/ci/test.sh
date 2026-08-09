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
