#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# The `typecheck` job's work, extracted so the Linux-native path and the
# containerised macOS path run ONE definition instead of two copies that drift.
# Same reasoning as scripts/check-bundle-size.sh.
#
# Run from the repo root. Assumes python + pip are already on PATH (supplied by
# actions/setup-python on a Linux runner, or by the python:3.12-slim image).
set -euo pipefail

pip install -r src/mcp/requirements.txt -r src/mcp/requirements-dev.txt
cd src/mcp && python -m mypy . --config-file ../../pyproject.toml
