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

import hashlib
from typing import Any

from core.retrieval.chunk_ids import ChunkIdAssigner
from core.retrieval.hype_index import hype_collection_name
from core.utils.swallowed import log_swallowed_error

# A verified-claim memory is a ``:Memory`` node with one recall document whose
# ``artifact_id`` is the memory id and whose id is ``{memory_id}_{h16}``, like
# every other row (forget spec §6.3). It has no ``:Artifact`` node by design
# (see VerifiedMemoryAdapter in app/services/forget/adapters.py). Documents
# written before had the id ``verified_memory_{memory_id}``; that form survives
# only in old sync exports and until the boot migration re-keys it.
LEGACY_VERIFIED_MEMORY_PREFIX = "verified_memory_"
VERIFIED_MEMORY_SOURCE = "verification"

# A verified memory is a general-knowledge claim kept for verification, not
# something the person told us, so general retrieval leaves it out unless the
# caller names the conversations domain (ruling D3, F113). Every one carries
# the marker, the legacy-id form included; ``$ne`` keeps rows without the key.
NOT_VERIFIED_MEMORY_WHERE: dict[str, Any] = {"memory_source_type": {"$ne": VERIFIED_MEMORY_SOURCE}}


def verified_memory_id(text: str) -> str:
    """A verified memory's id, from its claim: the same claim promoted on two
    machines is one memory, and a forgotten claim keeps its forgotten id."""
    return hashlib.sha256(("verified_memory\0" + " ".join(text.split()).lower()).encode("utf-8")).hexdigest()


def verified_memory_row_id(memory_id: str, text: str) -> str:
    return ChunkIdAssigner(memory_id).assign(None, text)


def is_verified_memory_row(row_id: str, meta: dict[str, Any] | None) -> bool:
    """A verified memory's recall document, in either id form."""
    return str(row_id).startswith(LEGACY_VERIFIED_MEMORY_PREFIX) or (
        (meta or {}).get("memory_source_type") == VERIFIED_MEMORY_SOURCE
    )


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


def remove_chunk_hype_rows(chroma: Any, base_collection: str, artifact_id: str, chunk_ids: list[str]) -> int:
    """Delete the HyPE questions written for some of an artifact's chunks.

    A question's id is ``{source_chunk_id}_hype_{n}``, so ownership by chunk
    is decided by id prefix, as it is by artifact.
    """
    hype = _hype_collection(chroma, base_collection)
    if hype is None or not chunk_ids:
        return 0
    prefixes = tuple(f"{cid}_hype_" for cid in chunk_ids)
    ids = [h for h in artifact_row_ids(hype, artifact_id, key="source_artifact_id") if h.startswith(prefixes)]
    if ids:
        hype.delete(ids=ids)
    return len(ids)
