# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Clearing a domain clears its lexical indexes too (audit F148).

``clear-domain`` deleted the artifacts and dropped the Chroma collection, but
the domain's BM25 and SPLADE corpora kept every chunk, so a keyword search
still returned text from a domain the user had cleared. Neo4j, Chroma and the
two index modules are faked; the route runs for real.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.helpers.fake_chroma import FakeChromaClient, FakeChromaCollection


def test_clearing_a_domain_removes_its_chunks_from_bm25_and_splade():
    import config
    from app.routers import kb_admin
    from app.services import content_lifecycle

    col = FakeChromaCollection(config.collection_name("finance"))
    col.upsert(ids=["a_0", "a_1"], documents=["x", "y"], embeddings=[[0.1], [0.2]],
               metadatas=[{"artifact_id": "a"}, {"artifact_id": "a"}])
    removed = {}

    def fake_index(name, holds):
        def remove_chunks(domain, ids):
            removed[name] = (domain, ids)
            return len(ids)
        return SimpleNamespace(remove_chunks=remove_chunks,
                               get_index=lambda d: SimpleNamespace(_doc_id_set=set(holds)))

    with (
        patch.object(kb_admin, "delete_artifacts_by_domain", return_value={"deleted": 1, "chunks": 2}),
        patch.object(kb_admin, "get_neo4j", return_value=MagicMock()),
        patch.object(kb_admin, "get_chroma", return_value=FakeChromaClient([col])),
        patch.object(content_lifecycle, "bm25", fake_index("bm25", {"a_0", "a_1", "orphan_9"})),
        patch.object(content_lifecycle, "sparse_index", fake_index("sparse", set())),
        patch.object(kb_admin, "_invalidate_scoped_safe"),
    ):
        out = asyncio.run(kb_admin.clear_domain("finance", kb_admin.ClearDomainRequest(confirm=True)))

    assert out["artifacts_deleted"] == 1
    assert removed["bm25"] == ("finance", ["a_0", "a_1", "orphan_9"])
    assert removed["sparse"] == ("finance", ["a_0", "a_1"])
