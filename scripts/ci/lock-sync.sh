#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# The `lock-sync` job's work, extracted so the Linux path (which used to be a
# job-level `container: python:3.12-slim` — a Linux-only feature that cannot
# run on the macOS runner) and the containerised macOS path run ONE definition.
#
# Run from the repo root inside python:3.12-slim (or any 3.12 with pip).
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root, whatever the caller's cwd was

pip install -q pip-tools==7.5.3
# Shared definition with `make lock-check` (scripts/check-lock-fresh.sh).
# --native because this script already runs inside the 3.12 environment.
bash scripts/check-lock-fresh.sh --native
