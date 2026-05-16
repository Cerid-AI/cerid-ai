#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Forbid `crypto.randomUUID()` in the web app — use the `uuid()` helper.

Graduates lesson ``tasks/lessons.md::"`crypto.randomUUID()` requires a
secure context"``. `crypto.randomUUID` is only defined under secure
contexts (HTTPS or `localhost`). When cerid is accessed via LAN IP over
plain HTTP, `crypto.randomUUID` is `undefined` and every call throws,
silently breaking all API calls that attach a request-ID header.

The repo's `src/web/src/lib/utils.ts::uuid()` falls back to
`crypto.getRandomValues()` (available in all contexts) so it works on
LAN. Every caller in the web layer must use that helper.

Allowlist: `utils.ts` itself + the *.test.tsx files that explicitly
test the secure-context path are exempt.

Usage::

    python3 scripts/lint-web-no-crypto-randomuuid.py
    python3 scripts/lint-web-no-crypto-randomuuid.py --warn-only
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPO_ROOT / "src" / "web" / "src"

# Exempt files — keep this list short. utils.ts implements the helper.
_ALLOWLIST = {
    Path("lib") / "utils.ts",
}

_PATTERN = re.compile(r"\bcrypto\s*\.\s*randomUUID\s*\(")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--warn-only", action="store_true")
    args = ap.parse_args()

    if not WEB_ROOT.exists():
        print(f"web root {WEB_ROOT} missing — skipping")
        return 0

    findings: list[str] = []
    for f in WEB_ROOT.rglob("*.t*"):
        # Cover .ts, .tsx
        if f.suffix not in (".ts", ".tsx"):
            continue
        if "node_modules" in f.parts or "__tests__" in f.parts:
            continue
        rel = f.relative_to(WEB_ROOT)
        if rel in _ALLOWLIST:
            continue
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            if _PATTERN.search(line):
                findings.append(
                    f"src/web/src/{rel}:{lineno}: "
                    f"crypto.randomUUID() — use uuid() from lib/utils.ts "
                    "(secure context required for randomUUID)"
                )

    if findings:
        print(
            "crypto.randomUUID() in web layer — graduates lessons.md L4:\n"
            "(undefined on LAN HTTP; uuid() helper works in all contexts)\n"
        )
        for f in findings:
            print(f"  {f}")
        return 0 if args.warn_only else 1

    print("web-no-crypto-randomuuid: web tree compliant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
