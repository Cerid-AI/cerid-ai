# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Forget subjects from outside: validated ids, and chunks resolved to passages.

Shared by the HTTP routes, the MCP tools and the SDK, so every surface
accepts the same ids and records the same subjects.
"""
from __future__ import annotations

import re
from typing import Any

from core.forget.registry import Subject
from core.retrieval.chunk_ids import chunk_artifact_id

KINDS = frozenset({"conversation", "artifact", "chunk", "memory"})
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
# A chunk id is its artifact's id, an underscore and a 16-hex suffix (or a
# positional suffix on rows not yet migrated).
_CHUNK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,160}$")


def parse_subject(kind: str, raw: str) -> Subject:
    """One subject, or ValueError naming what is wrong with it."""
    if kind == "conversation":
        from app.sync.user_state import validate_conversation_id
        return Subject(kind, validate_conversation_id(raw))
    if kind == "chunk":
        if not _CHUNK_ID_RE.match(raw or "") or not chunk_artifact_id(raw):
            raise ValueError(f"Invalid chunk id: {raw!r}")
        return Subject(kind, raw)
    if kind not in KINDS:
        raise ValueError(f"kind not supported: {kind}")
    if not _ID_RE.match(raw or ""):
        raise ValueError(f"Invalid {kind} id: {raw!r}")
    return Subject(kind, raw)


def parse_subjects(items: list[Any], kinds: frozenset[str] = KINDS) -> list[Subject]:
    """``items`` are objects or dicts with ``kind`` and ``id``."""
    if not items:
        raise ValueError("no subjects")
    pairs = [(getattr(i, "kind", None) or (i.get("kind") if isinstance(i, dict) else None),
              getattr(i, "id", None) or (i.get("id") if isinstance(i, dict) else None)) for i in items]
    bad = sorted({str(k) for k, _ in pairs if k not in kinds})
    if bad:
        raise ValueError(f"kind not supported: {', '.join(bad)}")
    return [parse_subject(str(k), str(v or "")) for k, v in pairs]


def resolve_passages(subjects: list[Subject]) -> list[Subject]:
    """Chunk subjects resolved to the passages retrieval serves (a child stands
    for its parent), so the registry, the read filter and the purge agree."""
    chunks = [s.id for s in subjects if s.kind == "chunk"]
    if not chunks:
        return subjects
    from app.services.forget.preview import passage_ids
    return [s for s in subjects if s.kind != "chunk"] + [Subject("chunk", c) for c in passage_ids(chunks)]
