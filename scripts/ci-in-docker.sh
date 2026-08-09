#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# Run a repo command inside a Linux container, from the repo root.
#
# WHY THIS EXISTS — the self-hosted runner is macOS.
# `mac-pro-1` is an Intel Mac provisioned as a DOCKER runner (which is why
# `preservation` and `benchmark-slo` succeed on it). Two things follow:
#
#   1. GitHub's job-level `container:` is Linux-only and simply refuses on a
#      macOS runner (actions/runner#1866), so the container has to be opened
#      here, in a step, rather than declared in the job.
#   2. `actions/setup-python` installs system-wide via sudo, and the runner
#      user has no passwordless sudo — that is the literal failure that broke
#      main on 2026-08-07: `sudo: a password is required`, before pip or mypy
#      ever ran.
#
# Running the work in a Linux container also fixes a defect nobody had hit yet:
# `requirements.lock` is resolved for linux/amd64, and this host is Intel macOS
# where onnxruntime publishes no wheel for current versions. Host-native
# execution would have installed a DIFFERENT dependency set than the one that
# ships — the same class of divergence `scripts/regen-lock.sh` already avoids by
# running pip-compile in python:3.12-slim.
#
# NOT used on Linux runners. There the native path is faster and cached
# (actions/setup-python's pip cache), so ci.yml selects between them on
# `runner.os` rather than containerising everything.
#
# Caches are bind-mounted from the host, so on the self-hosted runner they stay
# warm between runs — a persistent advantage the ephemeral hosted runners
# cannot have.
#
# Root inside the container is deliberate: pip/npm need to write to system
# locations, and Docker Desktop for macOS remaps bind-mount ownership to the
# host user, so this does not leave root-owned files in the working tree. That
# remapping is macOS-specific, which is exactly where this script runs.
#
# Usage: scripts/ci-in-docker.sh <image> <command> [args...]
#   scripts/ci-in-docker.sh python:3.12-slim bash scripts/ci/typecheck.sh
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <image> <command> [args...]" >&2
  exit 2
fi

image="$1"; shift

if ! command -v docker >/dev/null 2>&1; then
  echo "$0: docker is not on PATH — this runner cannot host the container path" >&2
  exit 1
fi

cache="${CI_CONTAINER_CACHE:-$HOME/.cache/cerid-ci}"
mkdir -p "$cache/pip" "$cache/npm"

# CI_SHADOW_DIRS: container-absolute paths to back with an anonymous volume so
# they live in the container's own filesystem instead of the macOS bind mount.
#
# REQUIRED FOR CORRECTNESS on any directory an install writes to, not merely a
# speed-up. `npm ci` inside the container resolves platform-specific optional
# dependencies for LINUX. Run unshadowed against a bind-mounted node_modules it
# rewrites the host's tree in place and evicts the darwin bindings, so the next
# native `npm test` on the host dies with "Cannot find native binding" for
# rolldown. That happened here on 2026-08-07 and cost a debugging cycle, made
# worse because `vitest --version` is a JS shim that still cheerfully reports
# darwin-x64 while the native binding underneath is gone.
#
# It is also a large speed-up, measured not guessed: the frontend suite over the
# bind mount spent 2266s of `environment` time on jsdom for 53 files and failed
# with 171 "Timeout waiting for worker to respond" — while reporting
# "53 passed (53)". The Docker VM had 24 CPUs and 94GiB free, so this was not a
# resource cap; node_modules is tens of thousands of tiny files and every read
# crosses virtiofs. Shadowed: 224 files, 2701 tests, 49s.
shadow_args=()
for d in ${CI_SHADOW_DIRS:-}; do
  shadow_args+=( -v "$d" )
done

echo "── ci-in-docker: $image :: $* ──"
[ "${#shadow_args[@]}" -gt 0 ] && echo "   shadowed (anon volume): ${CI_SHADOW_DIRS}"

# The command is passed as argv, never interpolated into a shell string, so
# quoting in the caller survives intact.
exec docker run --rm \
  -v "$PWD":/work -w /work \
  -v "$cache/pip":/root/.cache/pip \
  -v "$cache/npm":/root/.npm \
  ${shadow_args[@]+"${shadow_args[@]}"} \
  -e CI=true \
  -e GITHUB_ACTIONS="${GITHUB_ACTIONS:-}" \
  -e CI_EVENT_NAME="${CI_EVENT_NAME:-}" \
  "$image" "$@"
