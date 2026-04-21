# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Zip-bomb guard tests for Office parsers."""
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.parsers._zip_safety import assert_safe_zip


def _make_normal_zip(tmp_path: Path) -> str:
    path = tmp_path / "normal.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.xml", b"<doc>small</doc>")
    return str(path)


class TestZipBombGuard:
    def test_normal_zip_passes(self, tmp_path):
        assert_safe_zip(_make_normal_zip(tmp_path))

    def test_compression_ratio_bomb_rejected(self, tmp_path):
        """Verify the ratio branch triggers when compress_size=1 and file_size is large.

        zipfile normalises sizes on write for actual deflated entries, so we
        verify the logic directly by patching the cap to a value that the
        normally-compressed test entry will exceed.
        """
        path = tmp_path / "ratio_test.zip"
        # Write a highly-compressible entry (1000 repetitions of b"A").
        # DEFLATE compresses this to well under 50 bytes.
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("payload.txt", b"A" * 1000)

        import app.parsers._zip_safety as zs

        original_ratio = zs.MAX_COMPRESSION_RATIO
        # Set cap to 1 so any compression at all trips the guard
        zs.MAX_COMPRESSION_RATIO = 1
        try:
            with pytest.raises(HTTPException) as exc_info:
                assert_safe_zip(str(path))
            assert exc_info.value.status_code == 413
            assert "ratio" in exc_info.value.detail.lower() or "compression" in exc_info.value.detail.lower()
        finally:
            zs.MAX_COMPRESSION_RATIO = original_ratio

    def test_total_uncompressed_cap(self, tmp_path):
        path = tmp_path / "big.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("a.txt", b"A" * 1000)

        import app.parsers._zip_safety as zs

        original = zs.MAX_UNCOMPRESSED_BYTES
        zs.MAX_UNCOMPRESSED_BYTES = 500  # 500 bytes cap
        try:
            with pytest.raises(HTTPException) as exc_info:
                assert_safe_zip(str(path))
            assert exc_info.value.status_code == 413
            assert "uncompressed" in exc_info.value.detail.lower() or "zip-bomb" in exc_info.value.detail.lower()
        finally:
            zs.MAX_UNCOMPRESSED_BYTES = original

    def test_bad_zip_silently_returns(self, tmp_path):
        path = tmp_path / "bad.zip"
        path.write_bytes(b"not-a-zip-at-all")
        result = assert_safe_zip(str(path))
        assert result is None
