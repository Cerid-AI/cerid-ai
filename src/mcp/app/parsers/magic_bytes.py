# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Magic-byte validation for uploaded files.

Content-sniffing complements the extension check — a .pdf file that
actually contains ZIP bytes should be rejected before it reaches any
parser. Uses the pure-Python ``filetype`` library (no libmagic dep).
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

logger = logging.getLogger("ai-companion.magic_bytes")

# Extensions where magic-byte checking is meaningless (plain-text formats).
# These MUST match entries in config.SUPPORTED_EXTENSIONS; we pass them
# through without content sniffing.
_TEXT_ONLY_SUFFIXES: frozenset[str] = frozenset({
    ".md", ".txt", ".csv", ".tsv", ".json", ".jsonl",
    ".html", ".htm", ".xml", ".rst", ".log", ".ini", ".yaml", ".yml",
})

# Map from our canonical upload suffix to the set of filetype.guess()
# `.extension` values that are *acceptable* for that suffix. docx/xlsx/
# pptx all detect as "zip" in content, which is expected — the formats
# are ZIP containers. The parser layer does the container-content check.
_SUFFIX_TO_ACCEPTABLE_TYPES: dict[str, frozenset[str]] = {
    ".pdf": frozenset({"pdf"}),
    ".docx": frozenset({"zip", "docx"}),  # docx = newer filetype detections
    ".xlsx": frozenset({"zip", "xlsx"}),
    ".pptx": frozenset({"zip", "pptx"}),
    ".doc": frozenset({"doc", "cfb"}),  # CFB = Compound File Binary (old Office)
    ".xls": frozenset({"xls", "cfb"}),
    ".ppt": frozenset({"ppt", "cfb"}),
    ".epub": frozenset({"epub", "zip"}),
    ".mobi": frozenset({"mobi"}),
    ".png": frozenset({"png"}),
    ".jpg": frozenset({"jpg", "jpeg"}),
    ".jpeg": frozenset({"jpg", "jpeg"}),
    ".gif": frozenset({"gif"}),
    ".webp": frozenset({"webp"}),
    ".mp3": frozenset({"mp3"}),
    ".m4a": frozenset({"m4a", "mp4"}),
    ".wav": frozenset({"wav"}),
    ".zip": frozenset({"zip"}),
}


def validate_magic_bytes(suffix: str, content: bytes, *, filename: str) -> None:
    """Raise HTTPException(422) if the content bytes don't match the suffix.

    Text-only suffixes (``.md``, ``.txt``, etc.) bypass this check — they have
    no magic signature. Binary formats MUST be recognisable and match the
    acceptable-type set for their suffix.

    Parameters
    ----------
    suffix:
        Lowercase extension including the dot (e.g. ``".pdf"``).
    content:
        Full file content (already read into memory for the size check).
    filename:
        Original filename, used only for the error message.
    """
    if suffix in _TEXT_ONLY_SUFFIXES:
        return  # no magic signature — let the parser handle text

    import filetype  # lazy: avoids adding import latency when text-only

    kind = filetype.guess(content)
    if kind is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Could not identify content type of '{filename}'. "
                f"Declared extension was '{suffix}' but the file has no "
                "recognisable binary signature. Upload refused for safety."
            ),
        )

    acceptable = _SUFFIX_TO_ACCEPTABLE_TYPES.get(suffix)
    if acceptable is None:
        # Suffix is in config.SUPPORTED_EXTENSIONS but we haven't mapped
        # it here — fail open with a log rather than rejecting.
        # This is the "new supported extension added but magic map wasn't
        # updated" case; biased toward not breaking uploads.
        logger.warning(
            "magic_bytes.unmapped_suffix",
            extra={"suffix": suffix, "upload_filename": filename, "detected": kind.extension},
        )
        return

    if kind.extension not in acceptable:
        raise HTTPException(
            status_code=422,
            detail=(
                f"File '{filename}' has extension '{suffix}' but content "
                f"appears to be '{kind.extension}'. Refusing to ingest — "
                "this pattern is commonly used to smuggle zip bombs or "
                "malicious binaries through extension-only validation."
            ),
        )
