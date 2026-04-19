# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Magic-byte validation tests for upload security."""
import pytest
from fastapi import HTTPException

from app.parsers.magic_bytes import validate_magic_bytes

# Real PDF starts with %PDF-
_REAL_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
# PNG signature: 89 50 4E 47 0D 0A 1A 0A
_REAL_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
# A ZIP file (e.g. what a disguised zip-renamed-to-.pdf would be)
_ZIP_BYTES = (
    b"PK\x03\x04"  # Local file header signature
    + b"\x14\x00\x00\x00\x00\x00" + b"\x00" * 40  # minimal valid structure
)


class TestMagicBytesValidation:
    def test_text_only_suffix_bypasses(self):
        # Plain text with no magic — must not raise regardless of content
        validate_magic_bytes(".md", b"# Hello\n", filename="notes.md")
        validate_magic_bytes(".txt", b"random text", filename="t.txt")
        validate_magic_bytes(".json", b'{"k": 1}', filename="a.json")

    def test_real_pdf_accepted(self):
        validate_magic_bytes(".pdf", _REAL_PDF_BYTES, filename="real.pdf")

    def test_zip_disguised_as_pdf_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_magic_bytes(".pdf", _ZIP_BYTES, filename="bomb.pdf")
        assert exc_info.value.status_code == 422
        assert (
            "zip" in exc_info.value.detail.lower()
            or "content appears to be" in exc_info.value.detail.lower()
        )

    def test_pdf_renamed_to_docx_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_magic_bytes(".docx", _REAL_PDF_BYTES, filename="weird.docx")
        assert exc_info.value.status_code == 422

    def test_png_accepted(self):
        validate_magic_bytes(".png", _REAL_PNG_BYTES, filename="img.png")

    def test_unidentifiable_binary_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_magic_bytes(".pdf", b"random binary \x00\x01\x02", filename="x.pdf")
        assert exc_info.value.status_code == 422

    def test_unmapped_suffix_bypasses(self):
        # A suffix not in the acceptable-types map fails-open (logged, not rejected).
        # We pass ZIP bytes with an .rtf extension — filetype detects "zip" but
        # since ".rtf" has no entry in _SUFFIX_TO_ACCEPTABLE_TYPES we fail open.
        validate_magic_bytes(".rtf", _ZIP_BYTES, filename="x.rtf")
