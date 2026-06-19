# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""recategorize must move + verify ChromaDB chunks BEFORE flipping the Neo4j
domain, so a ChromaDB failure leaves both stores on the old domain instead of
drifting into a Neo4j-new / ChromaDB-old split (empty detail view)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import config
from app.routers import artifacts

_OLD, _NEW = config.DOMAINS[0], config.DOMAINS[1]
_CHUNKS = ["c1", "c2"]


def _artifact() -> dict:
    return {"domain": _OLD, "chunk_ids": json.dumps(_CHUNKS), "filename": "f.md"}


def _fetched() -> dict:
    return {
        "ids": list(_CHUNKS),
        "documents": ["d1", "d2"],
        "metadatas": [{"domain": _OLD}, {"domain": _OLD}],
    }


def _chroma_with(dest_verify_ids: list[str]):
    source = MagicMock()
    source.get.return_value = _fetched()
    dest = MagicMock()
    dest.get.return_value = {"ids": dest_verify_ids}
    chroma = MagicMock()
    # First get_or_create_collection → source (old), second → dest (new).
    chroma.get_or_create_collection.side_effect = [source, dest]
    return chroma, source, dest


def test_chroma_verify_failure_leaves_neo4j_untouched() -> None:
    chroma, source, dest = _chroma_with(dest_verify_ids=["c1"])  # c2 missing → verify fails
    with (
        patch.object(artifacts, "get_neo4j", return_value=object()),
        patch.object(artifacts, "get_chroma", return_value=chroma),
        patch.object(artifacts.graph, "get_artifact", return_value=_artifact()),
        patch.object(artifacts.graph, "recategorize_artifact") as mock_recat,
    ):
        with pytest.raises(RuntimeError):
            artifacts.recategorize("art-1", _NEW)

    mock_recat.assert_not_called()         # Neo4j never flipped
    source.delete.assert_not_called()      # originals not removed


def test_success_moves_chroma_then_flips_neo4j() -> None:
    chroma, source, dest = _chroma_with(dest_verify_ids=list(_CHUNKS))
    order: list[str] = []
    dest.add.side_effect = lambda **kw: order.append("chroma.add")
    source.delete.side_effect = lambda **kw: order.append("chroma.delete")

    def _flip(*_a, **_k) -> dict:
        order.append("neo4j.flip")
        return {"old_domain": _OLD, "new_domain": _NEW}

    with (
        patch.object(artifacts, "get_neo4j", return_value=object()),
        patch.object(artifacts, "get_chroma", return_value=chroma),
        patch.object(artifacts.graph, "get_artifact", return_value=_artifact()),
        patch.object(artifacts.graph, "recategorize_artifact", side_effect=_flip),
        patch.object(artifacts, "get_redis", return_value=None),
        patch.object(artifacts.cache, "log_event"),
    ):
        artifacts.recategorize("art-1", _NEW)

    # ChromaDB move (add + delete) strictly precedes the Neo4j flip.
    assert order == ["chroma.add", "chroma.delete", "neo4j.flip"]
