# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Content-addressed chunk ids.

A chunk's id is ``{artifact_id}_{h16}``, where ``h16`` hashes the artifact id,
the chunk level, the chunk's body and how many identical bodies came before it
in the same artifact. Re-chunking a document therefore cannot hand a forgotten
passage's id to different text, nor renumber it back into existence. The
``{artifact_id}_`` prefix stays because row ownership is decided by it
(``core/retrieval/artifact_rows.py``).

The body is the stored text without the decoration ingest adds in front of it
(the LLM contextual line and the ``Source: … | Domain: …`` header), so the id
depends on neither the filename, the domain nor a model's wording. Ingest and the
migration both hash the stored text, so they agree on every row.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

_CONTEXT_LINE = re.compile(r"\A\[[^\n]*\]\n")
_HEADER = re.compile(r"\A(?:Source|Domain|Category): [^\n]*\n\n")
_POSITIONAL = re.compile(r"_(?:chunk_\d+|parent_\d+|child_\d+_\d+)\Z")


def chunk_body(text: str) -> str:
    body = _CONTEXT_LINE.sub("", text, count=1)
    body = _HEADER.sub("", body, count=1)
    return " ".join(unicodedata.normalize("NFC", body).split())


def hash_level(level: str | None) -> str:
    return "parent" if level == "parent" else "child"


def content_chunk_id(artifact_id: str, level: str | None, body: str, occurrence: int) -> str:
    digest = hashlib.sha256(
        "\0".join((artifact_id, hash_level(level), body, str(occurrence))).encode("utf-8")
    ).hexdigest()
    return f"{artifact_id}_{digest[:16]}"


class ChunkIdAssigner:
    """Assigns ids to one artifact's chunks in their stored order."""

    def __init__(self, artifact_id: str) -> None:
        self._artifact_id = artifact_id
        self._seen: dict[tuple[str, str], int] = {}

    def assign(self, level: str | None, text: str) -> str:
        key = (hash_level(level), chunk_body(text))
        occurrence = self._seen.get(key, 0)
        self._seen[key] = occurrence + 1
        return content_chunk_id(self._artifact_id, key[0], key[1], occurrence)


def is_positional_chunk_id(chunk_id: str) -> bool:
    """True for the ids ingest wrote before content addressing."""
    return bool(_POSITIONAL.search(chunk_id))


def positional_artifact_id(chunk_id: str) -> str | None:
    """The artifact id a positional chunk id starts with."""
    match = _POSITIONAL.search(chunk_id)
    return chunk_id[: match.start()] if match else None


_CONTENT_SUFFIX = re.compile(r"_[0-9a-f]{16}\Z")


def chunk_artifact_id(chunk_id: str) -> str | None:
    """The artifact a chunk id belongs to, from either id form."""
    positional = positional_artifact_id(chunk_id)
    if positional:
        return positional
    match = _CONTENT_SUFFIX.search(chunk_id)
    return chunk_id[: match.start()] if match and match.start() > 0 else None
