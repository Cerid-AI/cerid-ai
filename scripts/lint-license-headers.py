#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Verify each file's SPDX header matches the license its PATH is supposed to carry.

The repository is deliberately not uniformly licensed (see the table in CONTRIBUTING.md):
an FSL-1.1-ALv2 product with Apache-2.0 carve-outs for the surfaces third parties depend
on, BUSL-1.1 plugin trees, and a proprietary premium tree. That model lives in prose and
in ~2000 per-file headers, and until this gate existed nothing checked that the two agreed.

They had already drifted. The FSL transition swept by extension and by directory, so two
first-party files created afterwards kept an Apache-2.0 header while sitting under the FSL
root and outside every carve-out — one of them `scripts/lint-license-denylist.py`, written
by the licensing work itself. A per-file header is what a license scanner reads, so a stray
one is not cosmetic: it is an actual grant of Apache terms over first-party product code.

What this checks: every tracked file that HAS an SPDX header must carry the one its path
implies. It deliberately does not require a header on files that lack one — that is a much
larger change, and the drift class worth blocking is a WRONG header, not a missing one.

    python scripts/lint-license-headers.py           # report and fail on mismatch
    python scripts/lint-license-headers.py --list    # also list every file + verdict
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# Longest-prefix-wins; the empty prefix is the repository default and MUST stay last.
# Mirrors the table in CONTRIBUTING.md — change both together.
LICENSE_MAP: list[tuple[str, str | None]] = [
    ("packages/sdk/", "Apache-2.0"),
    ("packages/cli/", "Apache-2.0"),
    ("packages/widget/", "Apache-2.0"),
    ("packages/extension/", "Apache-2.0"),
    ("plugins-premium/", None),  # proprietary; not distributed, header not policed here
    ("plugins/", "BUSL-1.1"),
    ("src/mcp/plugins/", "BUSL-1.1"),
    ("", "FSL-1.1-ALv2"),
]

# Vendored or generated third-party files. These keep their UPSTREAM license wherever they
# live, and sweeping them into a relicensing pass would misstate someone else's terms.
# Each entry needs a reason — an unexplained exemption is how a real drift hides.
THIRD_PARTY: dict[str, str] = {
    "packages/desktop/resources/notestore.proto": (
        "derived from threeplanetssoftware/apple_cloud_notes_parser (Apache-2.0)"
    ),
}
THIRD_PARTY_PREFIXES: dict[str, str] = {
    "tasks/archive/ux-audit-2026-04-24/": "generated Google Lighthouse reports (Apache-2.0, Google LLC)",
}

SKIP_SUBSTRINGS = ("node_modules/", "/dist/", "/out/", "package-lock.json")
HEADER_LINES = 5  # an SPDX line lives at the top or it is not a file header


def expected_for(path: str) -> str | None:
    best: tuple[int, str | None] = (-1, None)
    for prefix, lic in LICENSE_MAP:
        if path.startswith(prefix) and len(prefix) > best[0]:
            best = (len(prefix), lic)
    return best[1]


def is_third_party(path: str) -> str | None:
    if path in THIRD_PARTY:
        return THIRD_PARTY[path]
    for prefix, reason in THIRD_PARTY_PREFIXES.items():
        if path.startswith(prefix):
            return reason
    return None


def declared_in(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for _ in range(HEADER_LINES):
                line = fh.readline()
                if not line:
                    return None
                marker = "SPDX-License-Identifier:"
                if marker in line:
                    return line.split(marker, 1)[1].strip().split()[0]
    except OSError:
        return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="print every file with a header")
    args = ap.parse_args()

    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()

    mismatches: list[tuple[str, str, str]] = []
    checked = exempt = 0

    for path in tracked:
        if any(s in path for s in SKIP_SUBSTRINGS):
            continue
        declared = declared_in(path)
        if declared is None:
            continue
        reason = is_third_party(path)
        if reason:
            exempt += 1
            if args.list:
                print(f"  exempt   {path} ({declared}) — {reason}")
            continue
        expected = expected_for(path)
        if expected is None:
            continue
        checked += 1
        if declared != expected:
            mismatches.append((path, declared, expected))
        elif args.list:
            print(f"  ok       {path} ({declared})")

    print(f"[license-headers] checked {checked} headers, {exempt} third-party exemptions")
    if mismatches:
        print(f"\n✗ {len(mismatches)} file(s) declare a license their path does not grant:\n")
        for path, declared, expected in mismatches:
            print(f"  {path}\n      declares {declared}, path implies {expected}")
        print(
            "\nEither fix the header, or — if the file is genuinely third-party — add it to\n"
            "THIRD_PARTY in this script WITH a reason. Do not widen LICENSE_MAP to make a\n"
            "stray header pass; that changes the licensing model to match a typo."
        )
        return 1

    print("[license-headers] ✓ every SPDX header matches its path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
