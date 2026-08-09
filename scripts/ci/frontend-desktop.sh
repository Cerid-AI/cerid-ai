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
ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm ci
npm run typecheck
npm test
