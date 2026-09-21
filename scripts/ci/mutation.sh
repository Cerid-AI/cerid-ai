#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# The `mutation-check` job's work, extracted so the Linux-native path and the
# containerised macOS path run ONE definition (same shape as scripts/ci/test.sh).
#
# Run from the repo root.
set -euo pipefail

pip install -r src/mcp/requirements.txt
pip install pytest pytest-asyncio httpx pytest-cov respx 'fakeredis>=2.0,<3'

python scripts/mutation_check.py
