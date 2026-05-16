#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Forbid ``localhost`` inside Docker healthcheck commands.

Graduates lesson ``tasks/lessons.md::"Use 127.0.0.1 not localhost in
Alpine healthchecks"``. ``localhost`` resolves to ``::1`` (IPv6) in
Alpine containers; many cerid services only listen on ``0.0.0.0``
(IPv4) and the healthcheck returns "connection refused" even when the
service is fully up. Use ``127.0.0.1`` explicitly.

Checks every ``docker-compose*.yml`` (top-level + overlays) for
``healthcheck.test:`` entries containing the substring ``localhost``.

Usage::

    python3 scripts/lint-docker-healthcheck-localhost.py
    python3 scripts/lint-docker-healthcheck-localhost.py --warn-only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _scan(path: Path) -> list[str]:
    """Return findings — one per matching healthcheck line."""
    findings: list[str] = []
    in_healthcheck = False
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("healthcheck:"):
            in_healthcheck = True
            continue
        # When indentation drops below the healthcheck block depth, exit.
        # Heuristic: any new top-level service key resets state.
        if in_healthcheck and line and not line.startswith(" ") and not line.startswith("\t"):
            in_healthcheck = False
        if in_healthcheck and "test:" in stripped:
            if "localhost" in stripped:
                findings.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno}: "
                    f"healthcheck uses `localhost` (resolves to ::1 in Alpine; "
                    f"use 127.0.0.1 explicitly): {stripped[:120]}"
                )
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--warn-only", action="store_true")
    args = ap.parse_args()

    findings: list[str] = []
    for compose in REPO_ROOT.rglob("docker-compose*.yml"):
        # Skip vendored / archive content.
        if any(p in compose.parts for p in ("node_modules", ".git", "archive")):
            continue
        findings.extend(_scan(compose))

    if findings:
        print(
            "Docker healthcheck uses `localhost` — graduates lessons.md L3:\n"
            "(Alpine resolves to ::1; service likely binds 0.0.0.0; use 127.0.0.1)\n"
        )
        for f in findings:
            print(f"  {f}")
        return 0 if args.warn_only else 1

    print("docker-healthcheck-localhost: all compose files compliant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
