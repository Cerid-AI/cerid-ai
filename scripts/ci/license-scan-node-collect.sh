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
  # --ignore-scripts is load-bearing, not a speed-up. packages/desktop depends on
  # better-sqlite3, whose install script falls back to `node-gyp rebuild` when no
  # prebuild matches — and the containerised path runs in node:*-slim, which has
  # no Python. That path only executes on the self-hosted macOS runners, so when
  # the mac-pro pool died on 2026-08-17 it stopped running and nobody saw it rot;
  # it failed the moment the runners came back on 2026-08-30.
  #
  # Neither job needs a compiled native module: this one type-checks and runs unit
  # tests, and the license half only reads package.json metadata out of the
  # dependency tree. Whether better-sqlite3 actually BUILDS is proven where it
  # matters, by electron-build packaging and notarizing the real thing.
  (cd "$pkg" && npm ci --no-audit --no-fund --ignore-scripts)
  (cd "$pkg" && npx --yes license-checker@25.0.1 --production --json) > ".ci-artifacts/lc-$label.json"
  echo "::endgroup::"
done
