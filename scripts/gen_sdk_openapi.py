#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Generate docs/openapi-sdk-v1.json from the /sdk/v1/* FastAPI routes.

The /sdk/v1/* contract is the stable public API for SDK consumers.
Committing the rendered spec + a drift-check gate means any decorator
change that would silently reshape the consumer-facing schema has to
land as an explicit, reviewable diff to the baseline JSON.

Usage:
    python scripts/gen_sdk_openapi.py             # regenerate baseline
    python scripts/gen_sdk_openapi.py --check     # CI drift guard

Unlike ``gen_router_registry.py`` this generator imports the real MCP
runtime (it calls ``_build_sdk_spec()`` so the output matches what the
server publishes at /sdk/v1/openapi.json byte-for-byte). Run it from
repo root with ``.venv/bin/python``; CI installs runtime deps before
invoking it.

Parallel pattern to ``gen_router_registry.py`` — same --check idiom,
same unified-diff error output, same exit codes.
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = REPO_ROOT / "docs" / "openapi-sdk-v1.json"
MCP_SRC = REPO_ROOT / "src" / "mcp"

# Mirror gen_router_registry.py's sys.path setup so ``from app.routers...``
# resolves without requiring the caller to export PYTHONPATH.
sys.path.insert(0, str(MCP_SRC))


def _render() -> str:
    """Build the spec and serialize it stably for diffing."""
    from app.routers.sdk_openapi import _build_sdk_spec

    spec = _build_sdk_spec()
    return json.dumps(spec, sort_keys=True, indent=2) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="CI drift mode — exit 1 on mismatch")
    args = ap.parse_args()

    rendered = _render()

    if args.check:
        if not OUTPUT_FILE.exists():
            print(
                f"::error::{OUTPUT_FILE.relative_to(REPO_ROOT)} missing — "
                "run: python scripts/gen_sdk_openapi.py",
                file=sys.stderr,
            )
            return 1
        current = OUTPUT_FILE.read_text()
        if current != rendered:
            diff = "\n".join(
                difflib.unified_diff(
                    current.splitlines(),
                    rendered.splitlines(),
                    fromfile=str(OUTPUT_FILE.relative_to(REPO_ROOT)),
                    tofile="expected",
                    lineterm="",
                )
            )
            print(
                f"::error::{OUTPUT_FILE.relative_to(REPO_ROOT)} is out of date — "
                "regenerate with: python scripts/gen_sdk_openapi.py\n"
                + diff,
                file=sys.stderr,
            )
            return 1
        return 0

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(rendered)
    print(f"wrote {OUTPUT_FILE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
