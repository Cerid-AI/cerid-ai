# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Job records hold references, not words (forget spec §6.5): the memory-extract
job reads its text and writes its result under the conversation, and records
written before phase 6 are moved there by a migration."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest

from app.db.redis import turn_text
from core.processor.job import JobState


@pytest.fixture()
def redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.deps.get_redis", lambda: fake)
    return fake


async def _noop(_p: float) -> None:
    return None


_ENVELOPE = {
    "conversation_id": "c1", "timestamp": "2026-10-09T00:00:00Z", "memories_extracted": 1,
    "memories_stored": 1, "skipped_duplicates": 0, "results": [{"summary": "likes tea"}],
}


@pytest.mark.asyncio
async def test_the_result_goes_under_the_conversation_and_the_text_does_not_stay(redis):
    from app.processor.jobs.memory_extract import MemoryExtractJob, enqueue_payload, stored_result

    payload = enqueue_payload("I like tea", "c1", "m")
    with patch("app.agents.memory.extract_and_store_memories", new_callable=AsyncMock, return_value=_ENVELOPE) as run, \
         patch("app.deps.get_chroma"), patch("app.deps.get_neo4j"):
        result = await MemoryExtractJob(**payload).run(_noop)
    assert run.await_args.kwargs["response_text"] == "I like tea"
    assert "likes tea" not in json.dumps(result.metadata)  # the record never holds the summaries
    assert stored_result(payload["text_key"]) == _ENVELOPE
    assert "response_text" not in turn_text.get(redis, payload["text_key"])


@pytest.mark.asyncio
async def test_a_forget_during_extraction_leaves_no_result_behind(redis):
    """The purge erased the key while the job ran: the result must not recreate it."""
    from app.processor.jobs.memory_extract import MemoryExtractJob, enqueue_payload, stored_result

    payload = enqueue_payload("I like tea", "c1")

    async def forgotten_meanwhile(**_kw):
        turn_text.forget_conversation(redis, "c1")
        return _ENVELOPE

    with patch("app.agents.memory.extract_and_store_memories", side_effect=forgotten_meanwhile), \
         patch("app.deps.get_chroma"), patch("app.deps.get_neo4j"):
        result = await MemoryExtractJob(**payload).run(_noop)
    assert result.metadata["result_stored"] is False
    assert not redis.exists(payload["text_key"]) and stored_result(payload["text_key"]) is None


@pytest.mark.asyncio
async def test_a_text_that_is_gone_extracts_nothing(redis):
    from app.processor.jobs.memory_extract import MemoryExtractJob, enqueue_payload

    payload = enqueue_payload("I like tea", "c1")
    turn_text.forget_conversation(redis, "c1")
    with patch("app.agents.memory.extract_and_store_memories", new_callable=AsyncMock) as run:
        result = await MemoryExtractJob(**payload).run(_noop)
    run.assert_not_awaited()
    assert result.metadata["skipped"] == "text_gone"


def _old_record(redis, job_id: str, job_type: str, state: JobState, payload: dict, metadata: dict | None = None):
    redis.hset(f"cerid:proc:job:{job_id}", mapping={
        "id": job_id, "job_type": job_type, "state": state.value,
        "payload": json.dumps(payload), "metadata": json.dumps(metadata or {}),
    })


def test_records_written_before_phase_6_give_up_their_words(redis, monkeypatch):
    from app.services.job_text_migration import migrate_job_texts

    monkeypatch.setattr("core.forget.registry.is_forgotten", lambda kind, i: i == "gone")
    turn = {"user_message": "secret q", "assistant_response": "secret a", "model": "m"}
    _old_record(redis, "queued", "feedback_ingest", JobState.PENDING, {**turn, "conversation_id": "c1"})
    _old_record(redis, "failed", "feedback_ingest", JobState.FAILED, {**turn, "conversation_id": "c1"})
    _old_record(redis, "done", "feedback_ingest", JobState.COMPLETED, {**turn, "conversation_id": "c1"})
    _old_record(redis, "extracted", "memory_extract", JobState.COMPLETED,
                {"response_text": "secret", "conversation_id": "c2", "model": ""}, {"result": _ENVELOPE})
    _old_record(redis, "forgotten", "feedback_ingest", JobState.PENDING, {**turn, "conversation_id": "gone"})
    _old_record(redis, "other", "entity_extraction", JobState.PENDING, {"artifact_id": "a1"})

    assert migrate_job_texts(redis) == {"moved": 3, "dropped": 2}

    def payload(job_id: str) -> dict:
        return json.loads(redis.hget(f"cerid:proc:job:{job_id}", "payload"))

    for job_id in ("queued", "failed", "done", "extracted", "forgotten"):
        assert "secret" not in str(redis.hgetall(f"cerid:proc:job:{job_id}"))
    # a job that may still run keeps its turn, now where a forget reaches it
    assert turn_text.get(redis, payload("queued")["text_key"])["user_message"] == "secret q"
    assert turn_text.get(redis, payload("failed")["text_key"])["assistant_response"] == "secret a"
    assert payload("done")["text_key"] == "" and payload("forgotten")["text_key"] == ""
    from app.processor.jobs.memory_extract import stored_result
    assert stored_result(payload("extracted")["text_key"]) == _ENVELOPE
    assert payload("other") == {"artifact_id": "a1"}

    assert migrate_job_texts(redis) == {"moved": 0, "dropped": 0}  # a second run changes nothing
