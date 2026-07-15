# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase C — C3 Chroma bi-temporal metadata contract on the production write path.

Asserts extract_and_store_memories stamps the four-timestamp fields
(created_at / valid_from / valid_to) mirroring the eval-side field contract, and
— critically — that ``decay_anchor`` pins the Ebbinghaus age-anchor to ingestion
time so moving ``valid_from`` onto ``event_date`` does NOT shift decay (the i20b
slow-decay contract stays byte-identical). This metadata is ALWAYS stamped
(cheap + additive) — only the graph :Fact writes are flag-gated.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agents.fact_derivation import OPEN_INTERVAL


def _capturing_ingest_fn(captured: list[dict]):
    def _ingest(content: str, domain: str, metadata: dict | None = None) -> dict:
        captured.append(dict(metadata or {}))
        return {"status": "success", "artifact_id": f"art-{uuid.uuid4().hex[:8]}"}

    return _ingest


async def _store_one(memory: dict, *, observation_date: str | None):
    from core.agents.memory import extract_and_store_memories

    captured: list[dict] = []
    with patch(
        "core.agents.memory.extract_memories",
        new_callable=AsyncMock,
        return_value=[memory],
    ):
        with patch.dict(
            "config.features.FEATURE_TOGGLES",
            {"enable_memory_consolidation": False},
        ):
            await extract_and_store_memories(
                "x" * 200,
                conversation_id=f"c-{uuid.uuid4().hex[:8]}",
                chroma_client=MagicMock(),
                ingest_fn=_capturing_ingest_fn(captured),
                observation_date=observation_date,
            )
    assert len(captured) == 1
    return captured[0]


@pytest.mark.asyncio
async def test_valid_from_seeded_from_event_date():
    meta = await _store_one(
        {
            "content": "Attended a yoga class.",
            "memory_type": "conversational",
            "summary": "yoga",
            "event_date": "2026-03-01",
        },
        observation_date="2026-06-01",
    )
    assert meta["valid_from"] == "2026-03-01"           # event_date wins
    assert meta["event_date"] == "2026-03-01"
    assert meta["valid_to"] == OPEN_INTERVAL            # "" — still true
    assert meta["created_at"]                           # ingestion instant present


@pytest.mark.asyncio
async def test_valid_from_falls_back_to_observation_date():
    meta = await _store_one(
        {
            "content": "User prefers Rust.",
            "memory_type": "preference",
            "summary": "rust",
            "event_date": "",                            # no world date
        },
        observation_date="2026-06-01",
    )
    assert meta["valid_from"] == "2026-06-01"           # observation date


@pytest.mark.asyncio
async def test_valid_from_empty_when_nothing_known():
    meta = await _store_one(
        {
            "content": "Some undated fact.",
            "memory_type": "empirical",
            "summary": "x",
            "event_date": "",
        },
        observation_date=None,
    )
    assert meta["valid_from"] == ""


@pytest.mark.asyncio
async def test_decay_anchor_pins_ingestion_time_not_event_date():
    """decay_anchor == created_at (ingestion) even when valid_from is a far-past
    event_date — so calculate_memory_score's age anchor does not shift (i20b)."""
    meta = await _store_one(
        {
            "content": "Visited Paris.",
            "memory_type": "conversational",
            "summary": "paris",
            "event_date": "2019-01-01",                  # far in the past
        },
        observation_date="2026-06-01",
    )
    assert meta["valid_from"] == "2019-01-01"
    # The decay anchor is the ingestion instant, NOT the old event_date.
    assert meta["decay_anchor"] == meta["created_at"]
    assert meta["decay_anchor"] != meta["valid_from"]
    # It is a full ISO timestamp (has a time component), not a bare date.
    assert "T" in meta["decay_anchor"]


@pytest.mark.asyncio
async def test_metadata_stamped_regardless_of_fact_write_flag():
    """C3 metadata is always-on: identical whether ENABLE_FACT_WRITES is off/on
    (fact WRITES happen in the entity job, not here)."""
    mem = {
        "content": "Attended a yoga class.",
        "memory_type": "conversational",
        "summary": "yoga",
        "event_date": "2026-03-01",
    }
    with patch("config.features.ENABLE_FACT_WRITES", False):
        off = await _store_one(mem, observation_date="2026-06-01")
    with patch("config.features.ENABLE_FACT_WRITES", True):
        on = await _store_one(mem, observation_date="2026-06-01")
    for key in ("valid_from", "valid_to", "event_date"):
        assert off[key] == on[key]
