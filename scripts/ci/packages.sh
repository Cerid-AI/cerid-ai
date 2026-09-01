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

# Hash the committed widget bundle BEFORE anything rebuilds it, so the
# comparison below is against what is in the tree rather than against itself.
_hash_widget_dist() {
  if [ -d packages/widget/dist ]; then
    find packages/widget/dist -type f -exec md5sum {} + 2>/dev/null | sort -k2 | md5sum | cut -d' ' -f1
  fi
}
_WIDGET_DIST_BEFORE="$(_hash_widget_dist)"

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

# packages/extension: typecheck + build only, NOT `npm test`.
#
# Its test script is `playwright test`, which drives a real chromium against a
# running MCP server — neither exists in this container, and the spec skips
# itself when chromium is absent. A job whose test step always skips is the
# kind of green this repo keeps getting bitten by, so it is not pretended here.
#
# Typecheck and build are the parts that CAN be verified, and they matter: this
# package's typecheck was RED on main until 2026-08-31 and nothing ran it. Its
# tsconfig set `"types": ["chrome"]`, which is a restriction rather than an
# addition, so @types/node was invisible and tests/page-capture.spec.ts could
# not resolve `node:path` or `__dirname`.
echo "::group::packages/extension (typecheck + build; playwright needs a browser)"
(
  cd packages/extension
  npm ci --no-audit --no-fund
  npm run typecheck
  npm run build
)
echo "::endgroup::"

# The widget ships TWO bundles and `build` above emits both, but the CDN one is
# the artifact users embed and the one that silently broke. Assert it exists
# rather than trusting the exit code of a two-target build.
test -d packages/widget/dist || { echo "::error::widget build produced no dist/"; exit 1; }

# packages/widget/dist/ is COMMITTED, because it is the artifact that gets
# served: README documents https://cdn.cerid.ai/widget@0.1/cerid-widget.js and
# examples/index.html loads ../dist/cerid-widget.js directly. A committed
# artifact that nothing checks is the worst of both worlds — it was months
# behind its own source, which is how the CDN bundle was broken from
# 2026-06-08 with no check reporting it.
#
# The build above just regenerated it, so the tree should be unchanged. If it
# is not, someone edited source without rebuilding and the committed bundle no
# longer matches what it claims to be.
#
# This is only a fair gate because the build is REPRODUCIBLE — verified
# 2026-08-31 that two clean builds, and a macOS host versus a linux
# node:22-slim container, all produce byte-identical output for all three
# files. If that ever stops being true, delete this check rather than let it
# flake; a gate that fails for reasons unrelated to the change teaches people
# to ignore gates.
# Checksums, not `git diff`. The containerised path runs in node:*-slim, which
# has no git at all — `git diff --quiet` exits 127 there, and `if ! ...` reads
# that as "stale", so the first CI run of this check failed on a bundle that
# was perfectly current. A gate whose failure mode is indistinguishable from
# its success mode is worse than no gate.
#
# The committed files are bind-mounted, so hashing them before the build and
# again after answers the same question with nothing but coreutils.
if [ -n "${_WIDGET_DIST_BEFORE:-}" ]; then
  _widget_dist_after="$(_hash_widget_dist)"
  if [ "$_WIDGET_DIST_BEFORE" != "$_widget_dist_after" ]; then
    echo "::error::packages/widget/dist is stale — it does not match a fresh build."
    echo "  Run: (cd packages/widget && npm run build) and commit the result."
    echo "  before: $_WIDGET_DIST_BEFORE"
    echo "  after:  $_widget_dist_after"
    exit 1
  fi
  echo "widget dist: current (matches a fresh build)"
else
  echo "widget dist: NOT checked (no committed bundle to compare against)"
fi
ls -la packages/widget/dist | sed 's/^/  /'
