# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Every Chroma row an artifact owns, found from the stores themselves.

An artifact's Neo4j ``chunk_ids`` lists only its retrievable chunks. With
parent-child retrieval on, its parent chunks sit beside them in the same
collection and are listed nowhere else; its HyPE questions sit in the
``_hype`` companion collection, keyed by ``source_artifact_id``. A removal
that walks ``chunk_ids`` alone leaves both behind, and the parent chunk keeps
answering queries about content the user deleted.

Ownership is decided by the row id as well as the metadata: every id an ingest
writes starts with ``{artifact_id}_``, while caller-supplied metadata can carry
an ``artifact_id`` key of its own.
"""
from __future__ import annotations

from typing import Any

from core.retrieval.hype_index import hype_collection_name
from core.utils.swallowed import log_swallowed_error

# The verified-claim promotion path writes one document per ``:Memory`` node at
# this id, with ``artifact_id`` set to the memory id. It has no ``:Artifact``
# node by design (see VerifiedMemoryAdapter in app/services/forget/adapters.py).
VERIFIED_MEMORY_PREFIX = "verified_memory_"


def artifact_row_ids(collection: Any, artifact_id: str, *, key: str = "artifact_id") -> list[str]:
    """Ids of the rows in ``collection`` that belong to ``artifact_id``."""
    if not artifact_id:
        return []
    got = collection.get(where={key: artifact_id}, include=[])
    prefix = f"{artifact_id}_"
    return [cid for cid in got.get("ids") or [] if cid.startswith(prefix)]


def _hype_collection(chroma: Any, base_collection: str) -> Any | None:
    """The companion collection, or None when nothing was ever indexed for it.

    It is never created here.
    """
    try:
        return chroma.get_collection(name=hype_collection_name(base_collection))
    except Exception as exc:  # noqa: BLE001 — core does not import chromadb's error types
        if type(exc).__name__ != "NotFoundError":
            log_swallowed_error("core.retrieval.artifact_rows.hype_collection", exc)
        return None


def artifact_hype_row_ids(chroma: Any, base_collection: str, artifact_id: str) -> list[str]:
    """Ids of the artifact's HyPE questions in ``base_collection``'s companion."""
    hype = _hype_collection(chroma, base_collection)
    if hype is None:
        return []
    return artifact_row_ids(hype, artifact_id, key="source_artifact_id")


def remove_artifact_hype_rows(chroma: Any, base_collection: str, artifact_id: str) -> int:
    """Delete the artifact's HyPE questions from ``base_collection``'s companion."""
    hype = _hype_collection(chroma, base_collection)
    if hype is None:
        return 0
    ids = artifact_row_ids(hype, artifact_id, key="source_artifact_id")
    if ids:
        hype.delete(ids=ids)
    return len(ids)
