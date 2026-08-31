#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# The `packages` job's work: build, typecheck and test packages/widget and
# packages/sdk/typescript. ONE definition for the hosted-Linux and
# containerised-macOS paths, same as docker-gate.sh and frontend-desktop.sh.
#
# WHY THIS EXISTS
#
# Neither package had a build or test job in CI. `sdk-contract` pins the SDK
# against the OpenAPI spec and `license-scan` reads their dependency trees, but
# nothing ever ran `npm run build` or their suites. The widget's CDN bundle was
# broken from 2026-06-08 and no check reported it — it was found by hand.
#
# `build` is the point, not an extra. Both packages are DISTRIBUTED artifacts:
# the widget as an embeddable script tag, the SDK on npm. A typecheck that
# passes while the bundle fails to emit is exactly the gap that hid the CDN
# break, so the build runs here and its failure is the gate.
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root, whatever the caller's cwd was

for pkg in packages/widget packages/sdk/typescript; do
  echo "::group::$pkg"
  (
    cd "$pkg"
    npm ci --no-audit --no-fund
    npm run typecheck
    # `vitest` with no args watches; CI needs a single pass. The widget's
    # `test` script is bare `vitest`, the SDK's is already `vitest run`, so
    # pass --run explicitly rather than relying on either spelling.
    npm test -- --run
    npm run build
  )
  echo "::endgroup::"
done

# The widget ships TWO bundles and `build` above emits both, but the CDN one is
# the artifact users embed and the one that silently broke. Assert it exists
# rather than trusting the exit code of a two-target build.
test -d packages/widget/dist || { echo "::error::widget build produced no dist/"; exit 1; }
echo "widget dist:"
ls -la packages/widget/dist | sed 's/^/  /'
