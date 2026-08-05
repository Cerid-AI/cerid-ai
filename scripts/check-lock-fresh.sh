#!/usr/bin/env bash
# Is src/mcp/requirements.lock current with requirements.txt?
#
# Mirrors the ci.yml `lock-sync` job. Extracted so CI and `make prepush` run ONE
# definition — an inline CI script is a gate local can never reproduce, and the
# 2026-08-04 review found several such gaps at once.
#
# Seeds the output with the COMMITTED lock so pip-compile treats existing pins as
# constraints and only diffs when requirements.txt itself changes, rather than
# whenever a new in-range transitive publishes. Bumping to latest stays a
# deliberate ./scripts/regen-lock.sh action.
#
#   --native   resolve with the local pip-compile (CI, already in python:3.12-slim)
#   (default)  resolve inside python:3.12-slim via Docker
#
# The Docker default is not belt-and-braces: pip-compile on a mac-intel host
# resolves platform-specific wheels differently from linux CI (onnxruntime ships
# no macosx_x86_64 wheel), so a native local run reports drift that does not
# exist. Same reasoning as scripts/regen-lock.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

PIP_TOOLS_VERSION="7.5.3"
PYTHON_IMAGE="python:3.12-slim"
STALE_MSG="requirements.lock is out of date vs requirements.txt. Run ./scripts/regen-lock.sh from the repo root (Docker; reproducible across mac/linux)."

if [ "${1:-}" = "--native" ]; then
  cd src/mcp
  cp requirements.lock /tmp/requirements.lock
  pip-compile requirements.txt -o /tmp/requirements.lock \
    --generate-hashes --no-header --allow-unsafe
  diff requirements.lock /tmp/requirements.lock \
    || { echo "::error::${STALE_MSG}"; exit 1; }
  echo "✓ requirements.lock is current"
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required to check the lock deterministically." >&2
  echo "       (Or pass --native if you are already on linux/python3.12.)" >&2
  exit 1
fi

docker run --rm -v "$(pwd)/src/mcp:/work" -w /work "${PYTHON_IMAGE}" \
  sh -c "pip install --quiet pip-tools==${PIP_TOOLS_VERSION} && \
         cp requirements.lock /tmp/requirements.lock && \
         pip-compile requirements.txt -o /tmp/requirements.lock \
           --generate-hashes --no-header --allow-unsafe && \
         diff requirements.lock /tmp/requirements.lock" \
  || { echo "::error::${STALE_MSG}"; exit 1; }
echo "✓ requirements.lock is current"
