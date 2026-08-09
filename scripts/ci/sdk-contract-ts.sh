#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# TypeScript half of the `sdk-contract` job — see sdk-contract-python.sh for
# why the job is split into two scripts (different toolchain images).
#
# Run from the repo root. Assumes node + npm on PATH.
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root, whatever the caller's cwd was

cd packages/sdk/typescript
npm ci
npm run typecheck
npm test
