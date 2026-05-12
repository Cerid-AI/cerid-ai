# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pure-data container for an extracted email attachment (RAG Cycle C2.4).

The email parser (``app/parsers/email.py``) returns a list of these alongside
its body text so the service layer (``app/services/ingestion.py``) can
recursively ingest each attachment as its own ``Artifact`` and link it
back to the parent via a ``HAS_ATTACHMENT`` edge.

This module is intentionally dependency-free — no FastAPI, no Neo4j, no
ChromaDB — so it sits in ``core`` and respects the ``core / app``
import-linter boundary. The dataclass is frozen so a blob handed off to
the service layer can't be mutated mid-flight.
"""
from __future__ import annotations

from dataclasses import dataclass

# Per the locked design: 50 MB matches ``app/routers/upload.py::MAX_UPLOAD_BYTES``.
# Attachments larger than this are listed in the parent email's body text
# with a ``[skipped: too large]`` marker but never extracted.
EMAIL_ATTACHMENT_MAX_SIZE: int = 50 * 1024 * 1024


@dataclass(frozen=True)
class AttachmentBlob:
    """A single extracted email-attachment payload.

    Attributes
    ----------
    filename:
        Filename as declared by the email part's ``Content-Disposition``
        ``filename=`` parameter (or ``"(unnamed)"`` if absent). Used both
        for parser dispatch (extension lookup) and Neo4j edge metadata.
    content_bytes:
        Raw, fully-decoded bytes of the attachment. Caller may write
        these to a ``tempfile.NamedTemporaryFile`` before dispatching to
        a file-path-based parser.
    content_type:
        The MIME type from the email part's ``Content-Type`` header
        (e.g. ``"application/pdf"``). Stored on the ``HAS_ATTACHMENT``
        edge for downstream filtering.
    size:
        ``len(content_bytes)``. Stored separately so size-based decisions
        (skip, log, etc.) can be made without re-measuring.
    """

    filename: str
    content_bytes: bytes
    content_type: str
    size: int
