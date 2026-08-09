#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# The `security` job's work, extracted so the Linux-native path and the
# containerised macOS path run ONE definition instead of two copies that drift.
#
# Run from the repo root. Assumes python + pip on PATH (actions/setup-python on
# a Linux runner, or the python:3.12 image — full, not slim: detect-secrets
# walks the git tree, and the checkout is fetch-depth:0 for that reason).
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root, whatever the caller's cwd was

# The python:3.12 image bundles pip 25.0.1, which trips its own advisories
# (PYSEC-2026-1795/1796) in the pip-audit below. Upgrade past the fix rather
# than ignore — same precedent as scripts/audit-python-deps.sh's docker path.
# Hosted runners already carry a newer pip; the upgrade is a no-op there.
pip install -q --upgrade 'pip>=26.0'

pip install -q detect-secrets==1.5.0
# Canonical scan shared with `make ci-local` (scripts/detect-secrets-scan.sh)
# so the local gate matches this job — no drift in the exclude list.
bash scripts/detect-secrets-scan.sh

pip install -q bandit==1.9.4
bandit -r src/mcp/ -ll --skip B101,B615 -x src/mcp/tests

# Shared definition with `make security-local` (scripts/audit-python-deps.sh)
# so the curated ignore list, and its per-entry sunset dates, live in exactly
# one place. --native because this script installs the runtime right here.
pip install -q pip-audit==2.10.0
pip install -q -r src/mcp/requirements.txt
pip install -q --upgrade 'setuptools>=83.0.0'
bash scripts/audit-python-deps.sh --native

pip install -q dlint flake8
python -m flake8 --select=DUO138 src/mcp/
