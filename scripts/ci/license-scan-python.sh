#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# Python half of `license-scan`: resolve licences from the LOCK via PyPI
# metadata (~1.5s for 222 packages) instead of installing the tree, then run
# the compound-license-aware denylist. Scanning the lock scans what ships
# (audit finding GATE-08).
#
# Run from the repo root. Assumes python + pip on PATH.
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root, whatever the caller's cwd was

pip install -q pyyaml
python scripts/collect-lock-licenses.py --lock src/mcp/requirements.lock --output /tmp/pip-licenses.json
python scripts/lint-license-denylist.py --tool pip-licenses --input /tmp/pip-licenses.json --label python
