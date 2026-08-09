#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# Python half of the `sdk-contract` job, extracted so the Linux-native path and
# the containerised macOS path run ONE definition. The TypeScript half lives in
# scripts/ci/sdk-contract-ts.sh because the two halves need different images.
#
# Run from the repo root. Assumes python + pip on PATH.
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root, whatever the caller's cwd was

pip install -q -e "packages/sdk/python[test]"
pytest packages/sdk/python/tests/ -v
