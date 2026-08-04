#!/bin/bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Regenerate src/mcp/requirements.lock in a Linux container that matches
# CI exactly. Running pip-compile on a dev box directly produces a lock
# that drifts from CI whenever a pinned package has platform-specific
# wheel availability — e.g. onnxruntime 1.24.4 ships manylinux_x86_64
# and macosx_arm64 wheels but NOT macosx_x86_64, so a mac-intel dev
# resolves 1.23.2 while CI resolves 1.24.4.
#
# The fix is structural: always regen in the CI-identical environment.
# Contributors on any host (mac-arm, mac-intel, linux, windows-wsl) get
# the same lock output.
#
# Mirrors the lock-sync CI job at .github/workflows/ci.yml (container:
# python:3.12-slim, pip-tools==7.5.3). If CI changes those pins, change
# them here too.

set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_IMAGE="python:3.12-slim"
PIP_TOOLS_VERSION="7.5.3"

# Optional: names of packages to bump, e.g. `./scripts/regen-lock.sh cryptography`.
# With no arguments every package re-resolves to latest-in-range.
UPGRADE_ARGS="--upgrade"
if [ "$#" -gt 0 ]; then
  UPGRADE_ARGS=""
  for pkg in "$@"; do
    UPGRADE_ARGS="${UPGRADE_ARGS} --upgrade-package ${pkg}"
  done
  echo "Targeted bump:${UPGRADE_ARGS}"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required to regenerate the lock deterministically." >&2
  echo "       Install Docker Desktop (mac) or docker-ce (linux) and re-run." >&2
  exit 1
fi

echo "Regenerating src/mcp/requirements.lock in ${PYTHON_IMAGE}..."

# CI's lock-sync job seeds /tmp with the COMMITTED lock before re-resolving, so
# it preserves existing pins and only diffs when requirements.txt itself changes.
# Bumping to latest is therefore a deliberate act performed here, not something
# CI does daily. Pass package names to bump only those: a security fix should
# not drag ~50 unrelated upgrades along with it, and a surgical lock still
# satisfies lock-sync because seeded re-resolution keeps the other pins.
docker run --rm \
  -v "$(pwd)/src/mcp:/work" \
  -w /work \
  "${PYTHON_IMAGE}" \
  sh -c "pip install --quiet pip-tools==${PIP_TOOLS_VERSION} && \
         pip-compile requirements.txt -o requirements.lock \
           --generate-hashes --no-header --allow-unsafe ${UPGRADE_ARGS}"

echo "Done. Review with: git diff src/mcp/requirements.lock"
