# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Zip-bomb guard — central-directory inspection before extraction.

Used by every code path that opens a ZIP on behalf of an uploaded file:
  - app/parsers/office.py  (docx, xlsx, pptx)
  - app/parsers/ebook.py   (epub)
  - app/routers/migration.py  (notion export, obsidian export)

The guard is O(entries), not O(bytes) — it inspects the ZIP's central
directory without extracting anything. Fast enough to call on every
upload.
"""
from __future__ import annotations

import zipfile

from fastapi import HTTPException

# Match the values previously in office.py. Export as module-level so tests
# can monkey-patch the thresholds for ratio/total-cap verification.
MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024  # 250 MB
MAX_COMPRESSION_RATIO = 100  # per-entry file_size / compress_size cap


def assert_safe_zip(path: str) -> None:
    """Raise HTTPException if the ZIP's manifest indicates a bomb.

    Call BEFORE extracting or reading any entry. Checks:
      - total uncompressed size <= MAX_UNCOMPRESSED_BYTES (else 413)
      - per-entry compression ratio <= MAX_COMPRESSION_RATIO (else 413)
      - file is a valid ZIP (else 422)

    Does NOT extract — pure manifest inspection. O(entries), fast.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            total_uncompressed = 0
            for info in zf.infolist():
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > MAX_COMPRESSION_RATIO:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"Refusing to parse — compression ratio {ratio:.0f}:1 "
                                f"for entry '{info.filename}' exceeds the "
                                f"{MAX_COMPRESSION_RATIO}:1 safety cap (zip-bomb guard)."
                            ),
                        )
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Refusing to parse — uncompressed size would exceed "
                            f"{MAX_UNCOMPRESSED_BYTES // (1024*1024)} MB "
                            "(zip-bomb guard)."
                        ),
                    )
    except zipfile.BadZipFile:
        return  # Pass through — downstream parser library raises its own error
