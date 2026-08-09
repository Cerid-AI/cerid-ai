#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# Node half of `license-scan`, part 1 (collect): npm ci + license-checker over
# the four npm package roots that ship an artifact. Runs in a NODE image; the
# denylist check over its output runs in scripts/ci/license-scan-node-check.sh
# in a PYTHON image, because no slim image carries both toolchains and the two
# container invocations share the working tree via .ci-artifacts/.
#
# license-checker pinned via `npx --yes license-checker@25.0.1` — an unpinned
# resolve would silently pick up an output-shape change and defeat the scan.
#
# On the macOS path every package's node_modules MUST be shadowed
# (CI_SHADOW_DIRS) — see scripts/ci-in-docker.sh.
#
# Run from the repo root. Assumes node + npm on PATH.
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root, whatever the caller's cwd was

mkdir -p .ci-artifacts
for pkg in src/web packages/desktop packages/widget packages/sdk/typescript; do
  label=$(basename "$pkg")
  echo "::group::license-checker: $pkg"
  (cd "$pkg" && npm ci --no-audit --no-fund)
  (cd "$pkg" && npx --yes license-checker@25.0.1 --production --json) > ".ci-artifacts/lc-$label.json"
  echo "::endgroup::"
done
