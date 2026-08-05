# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Slice 5.3 — Track A enrichment backfill + Track B needs-review queue.

Pins:
- BackfillEnrichmentJob enriches bare artifacts' sub_category + tags via the
  classifier and writes Neo4j (update_artifact_taxonomy) + Chroma metadata.
- It NEVER changes domain (Track A) — including conversations.
- A drained backlog (no bare artifacts) self-idles.
- find_needs_review_artifacts surfaces the Track B queue.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _driver_returning(rows):
    driver = MagicMock()
    session = MagicMock()
    session.run.return_value = list(rows)
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = session
    return driver


def _chroma_with_chunks(docs, metas, ids):
    chroma = MagicMock()
    collection = MagicMock()
    collection.get.return_value = {"ids": ids, "documents": docs, "metadatas": metas}
    chroma.get_or_create_collection.return_value = collection
    return chroma, collection


async def _noop(_pct):
    return None


@pytest.mark.asyncio
async def test_drained_backlog_self_idles():
    from app.processor.jobs.backfill_enrichment import BackfillEnrichmentJob

    driver = _driver_returning([])  # no bare artifacts
    with (
        patch("app.deps.get_neo4j", return_value=driver),
        patch("app.deps.get_chroma", return_value=MagicMock()),
    ):
        result = await BackfillEnrichmentJob(batch_size=10, pace_s=0).run(_noop)

    assert result.metadata["drained"] is True
    assert result.metadata["enriched"] == 0


@pytest.mark.asyncio
async def test_enriches_bare_artifact_without_changing_domain():
    from app.processor.jobs import backfill_enrichment as mod
    from app.processor.jobs.backfill_enrichment import BackfillEnrichmentJob

    bare = [{"id": "a1", "domain": "finance", "filename": "trades.csv",
             "chunk_ids": json.dumps(["a1_chunk_0"])}]
    driver = _driver_returning([])  # session.run for taxonomy update returns []
    chroma, collection = _chroma_with_chunks(
        docs=["ETH order filled at $2,184.14"],
        metas=[{"domain": "finance", "artifact_id": "a1"}],
        ids=["a1_chunk_0"],
    )

    captured_taxonomy = {}

    def _fake_update_taxonomy(driver_, aid, sub_category, tags_json):
        captured_taxonomy.update(
            {"aid": aid, "sub_category": sub_category, "tags_json": tags_json}
        )
        return {}

    with (
        patch.object(mod, "_fetch_bare_artifacts", return_value=bare),
        patch("app.deps.get_neo4j", return_value=driver),
        patch("app.deps.get_chroma", return_value=chroma),
        patch("app.db.neo4j.taxonomy.update_artifact_taxonomy", _fake_update_taxonomy),
        patch("utils.metadata.ai_categorize", new_callable=AsyncMock,
              return_value={"sub_category": "trading", "tags": ["earnings"], "confidence": 0.9}),
    ):
        result = await BackfillEnrichmentJob(batch_size=10, pace_s=0).run(_noop)

    assert result.metadata["enriched"] == 1
    # Neo4j taxonomy write happened with the classifier's sub_category + tags.
    assert captured_taxonomy["sub_category"] == "trading"
    assert json.loads(captured_taxonomy["tags_json"]) == ["earnings"]
    # Chroma metadata updated in place (no add/delete = no collection move).
    collection.update.assert_called_once()
    _, ukwargs = collection.update.call_args
    assert ukwargs["metadatas"][0]["sub_category"] == "trading"
    assert ukwargs["metadatas"][0]["domain"] == "finance"  # domain untouched
    collection.add.assert_not_called()
    collection.delete.assert_not_called()


@pytest.mark.asyncio
async def test_conversations_enriched_in_place_domain_untouched():
    from app.processor.jobs import backfill_enrichment as mod
    from app.processor.jobs.backfill_enrichment import BackfillEnrichmentJob

    bare = [{"id": "c1", "domain": "conversations", "filename": "chat_2026.txt",
             "chunk_ids": json.dumps(["c1_chunk_0"])}]
    driver = _driver_returning([])
    chroma, collection = _chroma_with_chunks(
        docs=["we decided to ship friday"],
        metas=[{"domain": "conversations", "artifact_id": "c1"}],
        ids=["c1_chunk_0"],
    )

    with (
        patch.object(mod, "_fetch_bare_artifacts", return_value=bare),
        patch("app.deps.get_neo4j", return_value=driver),
        patch("app.deps.get_chroma", return_value=chroma),
        patch("app.db.neo4j.taxonomy.update_artifact_taxonomy", lambda *a, **k: {}),
        patch("utils.metadata.ai_categorize", new_callable=AsyncMock,
              return_value={"sub_category": "decisions", "tags": ["planning"], "confidence": 0.8}),
    ):
        result = await BackfillEnrichmentJob(batch_size=10, pace_s=0).run(_noop)

    assert result.metadata["enriched"] == 1
    _, ukwargs = collection.update.call_args
    # HARD CONSTRAINT — domain stays conversations; only metadata enriched.
    assert ukwargs["metadatas"][0]["domain"] == "conversations"
    assert ukwargs["metadatas"][0]["sub_category"] == "decisions"
    collection.add.assert_not_called()  # never moved collections


def test_find_needs_review_queue():
    from app.processor.jobs.backfill_enrichment import find_needs_review_artifacts

    rows = [{"id": "x1", "domain": "general", "filename": "f", "sub_category": "general"}]
    driver = _driver_returning(rows)
    out = find_needs_review_artifacts(driver, limit=50)
    assert out == rows
