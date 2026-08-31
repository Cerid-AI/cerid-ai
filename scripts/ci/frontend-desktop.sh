#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# The `frontend-desktop` job's work, extracted so the Linux-native path and the
# containerised macOS path run ONE definition.
#
# ELECTRON_SKIP_BINARY_DOWNLOAD: this job typechecks and runs the vitest unit
# tests — nothing executes the Electron runtime — so the ~100MB binary the
# postinstall would fetch on every cold run is pure waste on both paths.
#
# Run from the repo root. Assumes node + npm on PATH.
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root, whatever the caller's cwd was

cd packages/desktop
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
ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm ci --ignore-scripts
npm run typecheck
npm test
