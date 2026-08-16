#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Re-stamp ``latest-mac.yml`` to describe the DMG that actually ships.

electron-builder writes ``latest-mac.yml`` when it packages the DMG, recording
that file's sha512 and size. The workflow then notarizes and **staples** the
DMG, which appends the notarization ticket and changes both. Uploading the
untouched manifest alongside the modified DMG leaves the updater checking a
digest of a file that no longer exists: it downloads the real DMG, computes a
different sha512, and rejects the update.

Nothing fails visibly. The build is green, the release looks complete, and the
app just never updates — which is how v1.0.1-desktop and the first
v1.0.2-desktop build both shipped with it.

Windows needs no equivalent: nothing rewrites the installer after packaging,
so ``latest.yml`` stays accurate (verified against the shipped .exe).

Usage:
    python scripts/ci/restamp-mac-latest.py packages/desktop/release
"""
from __future__ import annotations

import base64
import hashlib
import re
import sys
from pathlib import Path


def sha512_b64(path: Path) -> str:
    """electron-updater compares base64 of the raw digest, not hex."""
    h = hashlib.sha512()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return base64.b64encode(h.digest()).decode("ascii")


def main() -> int:
    release_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "packages/desktop/release")
    manifest = release_dir / "latest-mac.yml"
    if not manifest.exists():
        print(f"::error::{manifest} not found — did package:mac run?")
        return 1

    dmgs = sorted(release_dir.glob("*.dmg"))
    if len(dmgs) != 1:
        print(f"::error::expected exactly one .dmg in {release_dir}, found {len(dmgs)}")
        return 1
    dmg = dmgs[0]

    digest = sha512_b64(dmg)
    size = dmg.stat().st_size
    text = manifest.read_text()

    before_sha = re.findall(r"sha512:\s*(\S+)", text)
    before_size = re.findall(r"size:\s*(\d+)", text)

    # Both the files[] entry and the top-level duplicate carry the digest.
    text = re.sub(r"(sha512:\s*)\S+", lambda m: m.group(1) + digest, text)
    text = re.sub(r"(size:\s*)\d+", lambda m: m.group(1) + str(size), text)
    manifest.write_text(text)

    changed = any(s != digest for s in before_sha) or any(
        int(s) != size for s in before_size
    )
    print(f"[restamp] {dmg.name}")
    print(f"[restamp]   sha512 {digest}")
    print(f"[restamp]   size   {size}")
    print(
        "[restamp] manifest updated — it described the pre-staple DMG"
        if changed
        else "[restamp] manifest already matched (nothing rewrote the DMG)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
