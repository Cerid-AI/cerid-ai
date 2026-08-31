#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# The `docker` job's work: hadolint both Dockerfiles, build both images, Trivy
# scan both. ONE definition for the hosted-Linux and self-hosted-macOS paths.
#
# Everything runs through the Docker CLI against whatever daemon the runner
# has, so unlike the other scripts/ci/*.sh this is NOT wrapped in
# ci-in-docker.sh — it IS the docker work. The hadolint/Trivy GitHub actions
# this replaces are Linux-only (Docker-container actions), which is why they
# could never run on the macOS runner; running the same pinned images by hand
# behaves identically on both.
#
# SHARED-DAEMON DISCIPLINE (the self-hosted runners share one Docker daemon
# with each other and with the dev stack):
#   * Image tags are namespaced by run id, so two runners building
#     concurrently can never clobber each other's tags.
#   * The run's tags are removed on exit (layer cache is left warm).
#   * NEVER `docker system prune` here — that daemon holds the dev stack's
#     volumes. The hosted-runner disk-free step stays in ci.yml, gated to
#     Linux, for exactly that reason.
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root, whatever the caller's cwd was

# Run the whole gate with an EMPTY docker config: the host config's
# credsStore (docker-credential-desktop) reads the macOS keychain, which
# hangs for the runner's non-GUI launchd context — buildkit's auth request
# never returns and every `load metadata` dies at its 60s deadline
# (runs 31211706362 / 31214282915, deterministic across retries, while the
# same build succeeded interactively). Anonymous Hub access is sufficient
# here (~4 metadata fetches/run against a 100/hr quota, verified untouched)
# and is what hosted runners have always used.
#
# macOS ONLY, as of 2026-08-31. On Linux this override also threw away the
# `docker login` the workflow performs, so the authenticated pull quota could
# never take effect — the gate discarded the credentials it had just been
# given. The keychain hang is a macOS credential-helper problem; Linux runners
# have no credsStore and want the login to survive.
if [ "$(uname -s)" = "Darwin" ]; then
  mkdir -p .ci-artifacts/docker-config
  printf '{}' > .ci-artifacts/docker-config/config.json
  export DOCKER_CONFIG="$PWD/.ci-artifacts/docker-config"
fi

HADOLINT_IMAGE="hadolint/hadolint:v2.12.0"   # matches hadolint-action@v3.3.0's default
TRIVY_IMAGE="ghcr.io/aquasecurity/trivy:0.69.3"  # matches the former trivy-action `version:`; Docker Hub's copy lives under `aquasec/`, GHCR under the org name
TAG="${GITHUB_RUN_ID:-local-$$}"
MCP_IMG="cerid-mcp-test-${TAG}"
WEB_IMG="cerid-web-test-${TAG}"

cleanup() { docker rmi -f "$MCP_IMG" "$WEB_IMG" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "::group::hadolint"
docker run --rm -i "$HADOLINT_IMAGE" hadolint --ignore DL3008 --ignore DL3013 --ignore DL3045 --ignore DL3059 - < src/mcp/Dockerfile
docker run --rm -i "$HADOLINT_IMAGE" hadolint --ignore DL3008 --ignore DL3018 --ignore DL3059 - < src/web/Dockerfile
echo "::endgroup::"

echo "::group::docker build"
# --pull --no-cache: the gate must measure a FRESH build. Both Dockerfiles run
# `apt-get upgrade`, so on a caching self-hosted daemon a stale apt layer
# resurrects OS CVEs that a fresh build already fixes — CVE-2026-55199
# (libssh2, fixed in deb13u1) did exactly that on this gate's first local run.
# Hosted runners were implicitly cache-free; this pins the same semantics
# everywhere. Layer-cache speed is deliberately traded away here — the gate
# is merge-time only, and self-hosted minutes are free.
#
# One retry per build: --pull makes every run resolve base-image metadata
# against Docker Hub, and a single 60s registry stall (DeadlineExceeded on
# `load metadata`) killed run 31211706362 before the build proper started.
# A real Dockerfile failure fails identically twice; a registry blip does not.
build_with_retry() {
  docker build "$@" || { echo "::warning::docker build failed once — retrying (registry stalls are the common cause)"; docker build "$@"; }
}
build_with_retry --pull --no-cache -t "$MCP_IMG" -f src/mcp/Dockerfile .
build_with_retry --pull --no-cache -t "$WEB_IMG" src/web/
echo "::endgroup::"

# The ignore list stays curated here (one place, both paths). Unfixed
# base-image CVEs are excluded wholesale by --ignore-unfixed; entries below are
# the fixed-but-accepted ones with their rationale.
mkdir -p .ci-artifacts
cat > .ci-artifacts/trivyignore <<'EOF'
# LangChain/LangGraph CVEs requiring major version migration (Phase 11)
CVE-2026-26013
CVE-2025-64439
CVE-2026-27794
# glibc heap corruption — no fix available in Debian 13 yet
CVE-2026-0861
# wheel privilege escalation — build-time only, not exploitable at runtime
CVE-2026-24049
# systemd CVE in Debian 13 base — D-Bus RegisterMachine not reachable in container
CVE-2026-4105
# libxml2 CVEs in Alpine base — nginx serves static files only, no XML parsing
CVE-2025-32414
CVE-2025-32415
CVE-2025-49794
CVE-2025-49795
CVE-2025-49796
CVE-2025-6021
# jaraco.context path traversal via tar — build-time pip dependency, not runtime-exploitable
CVE-2026-23949
# ncurses buffer overflow — no fix in Debian 13, container has no interactive terminals
CVE-2025-69720
# nghttp2 DoS via malformed HTTP/2 — internal container traffic only, no external exposure
CVE-2026-27135
# systemd arbitrary code execution — D-Bus not reachable in container context
CVE-2026-29111
# openssl DoS via NULL pointer — Debian base image, pending 3.5.5-1 patch
CVE-2026-28390
# perl-base Archive::Tar symlink — pulled into Debian base layer.
# We never invoke perl from application code (no Archive::Tar calls,
# no perl scripts in container). Trivy flags it transitively. No
# upstream fix in Debian 13 yet. Re-eval 2026-11-30 (verified still present in the base image 2026-08-31).
CVE-2026-42496
# perl-base heap buffer overflow during compilation — same surface
# as CVE-2026-42496; perl interpreter never executes in container.
# No upstream fix; Re-eval 2026-11-30 (verified still present in the base image 2026-08-31).
CVE-2026-8376
# perl-base Archive::Tar memory exhaustion (HIGH) — same surface;
# no Archive::Tar usage from application code. No upstream fix;
# Re-eval 2026-11-30 (verified still present in the base image 2026-08-31).
CVE-2026-9538
# perl-base Archive::Tar hardlink — companion to CVE-2026-42496 (which
# was the symlink variant). Same suppression rationale; no Archive::Tar
# usage from application code. No upstream fix; Re-eval 2026-11-30 (verified still present in the base image 2026-08-31).
CVE-2026-42497
# perl-base perl-IO-Compress arbitrary code execution (HIGH) — same
# perl-base surface as the entries above; the perl interpreter never
# executes in the container (no perl scripts, no IO::Compress usage
# from application code). No upstream fix in Debian 13 yet.
# Re-eval 2026-11-30 (verified still present in the base image 2026-08-31).
CVE-2026-48962
# perl-base IO::Uncompress::Unzip CPU exhaustion on crafted zip —
# same never-executes-perl surface as the entries above. No upstream
# fix in Debian 13 as of 2026-06-10. Re-eval 2026-11-30 (verified still present in the base image 2026-08-31).
CVE-2026-48959
# chromadb 1.5.9 pre-auth code injection via trust_remote_code=true on
# the collections endpoint. Not reachable — trust_remote_code is set
# nowhere in the codebase (grep-verified) and the Chroma server binds
# loopback-only. Mirrors the pip-audit ignore in the security job.
# Re-eval 2026-09-30 (chromadb patch cadence).
CVE-2026-45829
EOF

# Trivy runs from its own container against the shared daemon via the socket.
# The DB cache mount keeps the vulnerability DB warm between runs on the
# self-hosted host (hosted runners are ephemeral and re-download; unchanged
# from the action's behaviour there).
trivy_scan() {
  local image_ref="$1"; shift
  docker run --rm \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$HOME/.cache/cerid-ci/trivy":/root/.cache/trivy \
    -v "$PWD/.ci-artifacts/trivyignore":/trivyignore:ro \
    "$TRIVY_IMAGE" image \
    --severity CRITICAL,HIGH --exit-code 1 --ignore-unfixed \
    --ignorefile /trivyignore "$@" "$image_ref"
}

echo "::group::trivy scan (mcp)"
# Trivy's default 5m deadline is not enough for this image: the scan died at
# 305s on torch/lib/libtorch_cuda.so the first time this job actually ran
# after 2026-07-24, so it reported NOTHING rather than reporting clean. A scan
# that times out is not a scan that passed.
trivy_scan "$MCP_IMG" --timeout 20m
echo "::endgroup::"

echo "::group::trivy scan (web)"
trivy_scan "$WEB_IMG"
echo "::endgroup::"
