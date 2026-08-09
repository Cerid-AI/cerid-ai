#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# Node half of `license-scan`, part 2 (check): run the compound-license-aware
# denylist over the license-checker JSON that license-scan-node-collect.sh
# wrote to .ci-artifacts/. Split from the collector because the checker needs
# python and the collector needs node — see the collector's header.
#
# Run from the repo root. Assumes python + pip on PATH.
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root, whatever the caller's cwd was

pip install -q pyyaml
for pkg in src/web packages/desktop packages/widget packages/sdk/typescript; do
  label=$(basename "$pkg")
  test -s ".ci-artifacts/lc-$label.json" || { echo "missing .ci-artifacts/lc-$label.json — collector did not run"; exit 1; }
  python scripts/lint-license-denylist.py --tool license-checker --input ".ci-artifacts/lc-$label.json" --label "$label"
done
