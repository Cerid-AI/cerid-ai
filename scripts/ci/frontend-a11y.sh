#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# The `frontend-a11y` job's work, extracted so the Linux-native path and the
# containerised macOS path run ONE definition. On the macOS path node_modules
# MUST be shadowed (CI_SHADOW_DIRS) — see scripts/ci-in-docker.sh for why that
# is correctness, not a speed-up.
#
# Run from the repo root. Assumes node + npm on PATH.
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root, whatever the caller's cwd was

cd src/web
npm ci
npm test -- --run --reporter=verbose -t "axe-clean"
