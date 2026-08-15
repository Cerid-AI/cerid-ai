#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Every manifest that claims to carry THE product version must agree with it.

`pyproject.toml` is the single source of truth (`core/utils/version.py::
get_version` reads it). `src/web/package.json` had drifted to 0.93.10 against a
1.0.1 product and nothing noticed for seven minor versions — which mattered
once that version became the Sentry release baked into the built web app by
`vite.config.ts`. A stale value there silently mislabels every error report.

Independently-versioned artifacts are deliberately NOT checked: the SDK,
widget, extension and e2e harness are published on their own semver and are
expected to differ.

Usage:
    python scripts/lint-version-consistency.py --check
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Manifests that must equal the product version.
TRACKED = [
    Path("src/web/package.json"),
    Path("packages/desktop/package.json"),
]

# Published separately, on their own semver — listed so the omission is a
# recorded decision rather than an oversight.
INDEPENDENT = {
    "packages/sdk/typescript/package.json": "published to npm on its own semver",
    "packages/widget/package.json": "embeddable widget, versioned independently",
    "packages/extension/package.json": "browser extension, versioned independently",
    "tests/beta/e2e/package.json": "test harness, not shipped",
}


def product_version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.parse_args()

    expected = product_version()
    problems: list[str] = []

    for rel in TRACKED:
        path = ROOT / rel
        if not path.exists():
            # internal_only manifests are absent from the mirror; not an error.
            continue
        got = json.loads(path.read_text()).get("version")
        if got != expected:
            problems.append(f"  {rel}: {got!r} != pyproject {expected!r}")

    if problems:
        print(
            "::error::[version-consistency] manifest(s) disagree with the "
            f"product version {expected}:"
        )
        print("\n".join(problems))
        print(
            "\nUpdate the manifest, or — if it is meant to version "
            "independently — move it to INDEPENDENT in this script with a "
            "reason."
        )
        return 1

    print(
        f"[version-consistency] OK — {len(TRACKED)} tracked manifest(s) at "
        f"{expected}; {len(INDEPENDENT)} independently versioned by decision."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
