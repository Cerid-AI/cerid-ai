#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# The `frontend` job's work, extracted so the Linux-native path and the
# containerised macOS path run ONE definition instead of two copies that drift.
#
# Run from the repo ROOT. The npm work happens in src/web (which the job used
# to express as a job-level `defaults.run.working-directory`); that is now held
# here so both callers agree on it. check-bundle-size.sh self-locates, so it is
# indifferent to cwd.
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root, whatever the caller's cwd was
root="$PWD"

cd "$root/src/web"
npm ci
npx tsc -b
npm run lint
npm test
npx vite build

bash "$root/scripts/check-bundle-size.sh"

cd "$root/src/web"

# Production dependencies: BLOCKING.
npm audit --audit-level=high --omit=dev

# Dev toolchain: ADVISORY. This mirrors `continue-on-error: true` on the
# corresponding ci.yml step. Collapsing the two audits into one script under
# `set -e` would silently promote this one to blocking, which is a stricter
# gate than the job has ever had — the failure would land on whoever next
# bumped a dev dependency, looking like their fault.
npm audit --audit-level=high || echo "::warning::dev-toolchain npm audit reported findings (advisory, non-blocking)"
