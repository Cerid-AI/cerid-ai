# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for ``app.processor.jobs.feedback_ingest`` + the queued
``POST /ingest/feedback`` endpoint contract.

The ingestion service is monkeypatched — these tests exercise the job
wrapper contract (content/metadata shape, best-effort tail, failure
propagation) and the endpoint's 202 ack, not the chunk/embed/store
pipeline, which ``test_ingestion.py`` already covers.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.processor.jobs.feedback_ingest import (
    FeedbackIngestJob,
    enqueue_feedback_ingest_job,
)
from core.processor.priority import Priority


async def _noop_progress(_pct: float) -> None:
    return None


@pytest.fixture()
def redis(monkeypatch):
    """A real Redis in memory: the turn is stored and read back through it."""
    import fakeredis

    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.deps.get_redis", lambda: fake)
    return fake


def _job(redis, **overrides) -> FeedbackIngestJob:
    from app.db.redis import turn_text

    turn = {"user_message": "What is Cerid?", "assistant_response": "A personal AI knowledge companion."}
    turn.update({k: overrides.pop(k) for k in list(overrides) if k in turn})
    kwargs = {"model": "test-model", "conversation_id": "convo-1234-abcd", **overrides}
    key = turn_text.new_key(kwargs["conversation_id"])
    turn_text.put(redis, key, turn)
    return FeedbackIngestJob(text_key=key, **kwargs)


# ── job contract ──────────────────────────────────────────────────────────

def test_job_registered_in_default_registry():
    from app.processor.worker import build_default_registry

    registry = build_default_registry()
    assert registry.get("feedback_ingest") is FeedbackIngestJob


def test_job_is_medium_priority_and_free(redis):
    job = _job(redis)
    assert job.priority is Priority.MEDIUM
    estimate = job.estimate_cost()
    assert estimate.estimated_tokens_in == 0
    assert float(estimate.estimated_usd) == 0.0


def test_the_job_record_holds_a_reference_not_the_turn(redis):
    """Worker re-instantiates as job_class(**record.payload); the record lives
    14 days, so it must not hold the words (forget spec §6.5)."""
    job_id = enqueue_feedback_ingest_job(
        user_message="my secret", assistant_response="noted", model="m", conversation_id="c1", redis_client=redis,
    )
    stored = redis.hgetall(f"cerid:proc:job:{job_id}")
    assert "my secret" not in str(stored)
    payload = __import__("json").loads(stored["payload"])
    clone = FeedbackIngestJob(**payload)
    assert clone._conversation_id == "c1"
    from app.db.redis import turn_text
    assert turn_text.get(redis, payload["text_key"])["user_message"] == "my secret"


@pytest.mark.asyncio
async def test_run_ingests_turn_into_conversations_domain(monkeypatch, redis):
    monkeypatch.setattr("config.ENABLE_HALLUCINATION_CHECK", False)
    captured: dict = {}

    def fake_ingest(content, domain, metadata):
        captured.update(content=content, domain=domain, metadata=metadata)
        return {"status": "success", "artifact_id": "art:fb-1"}

    job = _job(redis)
    with (
        patch("app.services.ingestion.ingest_content", side_effect=fake_ingest),
        patch("core.utils.cache.log_event") as mock_log_event,
    ):
        result = await job.run(progress_cb=_noop_progress)

    assert captured["domain"] == "conversations"
    assert "User: What is Cerid?" in captured["content"]
    assert "Assistant (test-model):" in captured["content"]
    assert captured["metadata"]["conversation_id"] == "convo-1234-abcd"
    assert captured["metadata"]["filename"].startswith("chat_convo-12_")
    assert result.metadata["artifact_id"] == "art:fb-1"
    assert result.metadata["status"] == "success"
    mock_log_event.assert_called_once()
    assert not redis.exists(job._text_key)  # ingested: the stored turn is gone


@pytest.mark.asyncio
async def test_a_turn_that_is_gone_is_skipped(monkeypatch, redis):
    """Forgotten (the purge erased it) or expired: nothing is ingested."""
    monkeypatch.setattr("config.ENABLE_HALLUCINATION_CHECK", False)
    job = _job(redis)
    redis.delete(job._text_key)
    with patch("app.services.ingestion.ingest_content") as ingest:
        result = await job.run(progress_cb=_noop_progress)
    assert result.metadata["skipped"] == "text_gone"
    ingest.assert_not_called()


@pytest.mark.asyncio
async def test_run_awaits_hallucination_check_when_enabled(monkeypatch, redis):
    monkeypatch.setattr("config.ENABLE_HALLUCINATION_CHECK", True)
    job = _job(redis)
    with (
        patch(
            "app.services.ingestion.ingest_content",
            return_value={"status": "success", "artifact_id": "art:fb-2"},
        ),
        patch("app.deps.get_chroma", return_value=MagicMock()),
        patch("app.deps.get_neo4j", return_value=MagicMock()),
        patch("core.utils.cache.log_event"),
        patch(
            "core.agents.hallucination.check_hallucinations",
            new_callable=AsyncMock,
        ) as mock_check,
    ):
        await job.run(progress_cb=_noop_progress)

    mock_check.assert_awaited_once()
    assert mock_check.await_args.kwargs["conversation_id"] == "convo-1234-abcd"
    assert mock_check.await_args.kwargs["response_text"] == "A personal AI knowledge companion."


@pytest.mark.asyncio
async def test_run_best_effort_tail_never_fails_the_job(monkeypatch, redis):
    """Audit-log / hallucination failures are swallowed (with
    log_swallowed_error) — the persisted ingest must not be retried because a
    side-channel hiccuped."""
    monkeypatch.setattr("config.ENABLE_HALLUCINATION_CHECK", True)
    job = _job(redis)
    with (
        patch(
            "app.services.ingestion.ingest_content",
            return_value={"status": "success", "artifact_id": "art:fb-3"},
        ),
        patch("app.deps.get_chroma", return_value=MagicMock()),
        patch("app.deps.get_neo4j", return_value=MagicMock()),
        patch("core.utils.cache.log_event", side_effect=RuntimeError("redis down")),
        patch(
            "core.agents.hallucination.check_hallucinations",
            new_callable=AsyncMock, side_effect=RuntimeError("nli down"),
        ),
    ):
        result = await job.run(progress_cb=_noop_progress)

    assert result.metadata["artifact_id"] == "art:fb-3"


@pytest.mark.asyncio
async def test_run_propagates_ingest_failure_for_retry(monkeypatch, redis):
    """The ingest itself failing must raise so the worker retries — content
    dedup makes the re-run converge instead of duplicating — and the turn
    stays stored for that retry."""
    monkeypatch.setattr("config.ENABLE_HALLUCINATION_CHECK", False)
    job = _job(redis)
    with patch(
        "app.services.ingestion.ingest_content",
        side_effect=RuntimeError("chroma down"),
    ):
        with pytest.raises(RuntimeError):
            await job.run(progress_cb=_noop_progress)
    assert redis.exists(job._text_key)


def test_a_failed_enqueue_leaves_no_turn_behind(redis):
    with patch("app.db.redis.processor_queue.enqueue_job", side_effect=RuntimeError("queue down")):
        with pytest.raises(RuntimeError):
            enqueue_feedback_ingest_job(
                user_message="hi", assistant_response="hello", conversation_id="c1", redis_client=redis,
            )
    assert not list(redis.scan_iter(match="cerid:proc:text:*"))


# ── endpoint contract ─────────────────────────────────────────────────────

def _make_app() -> FastAPI:
    from app.routers.ingestion import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client():
    return TestClient(_make_app())


_REQ = {
    "user_message": "What is Cerid?",
    "assistant_response": "A personal AI knowledge companion.",
    "model": "test-model",
    "conversation_id": "convo-1234-abcd",
}


class TestFeedbackEndpoint:
    def test_skips_when_feedback_loop_disabled(self, client, monkeypatch):
        monkeypatch.setattr("config.ENABLE_FEEDBACK_LOOP", False)
        resp = client.post("/ingest/feedback", json=_REQ)
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    def test_acks_202_and_enqueues_job(self, client, monkeypatch):
        monkeypatch.setattr("config.ENABLE_FEEDBACK_LOOP", True)
        with patch(
            "app.processor.jobs.feedback_ingest.enqueue_feedback_ingest_job",
            return_value="job-fb-1",
        ) as mock_enqueue:
            resp = client.post("/ingest/feedback", json=_REQ)
        assert resp.status_code == 202
        assert resp.json() == {"status": "queued", "job_id": "job-fb-1"}
        kwargs = mock_enqueue.call_args.kwargs
        assert kwargs["user_message"] == _REQ["user_message"]
        assert kwargs["conversation_id"] == _REQ["conversation_id"]

    def test_logs_conversation_metrics_synchronously(self, client, monkeypatch):
        monkeypatch.setattr("config.ENABLE_FEEDBACK_LOOP", True)
        with (
            patch(
                "app.processor.jobs.feedback_ingest.enqueue_feedback_ingest_job",
                return_value="job-fb-2",
            ),
            patch("app.routers.ingestion.get_redis", return_value=MagicMock()),
            patch("core.utils.cache.log_conversation_metrics") as mock_metrics,
        ):
            resp = client.post(
                "/ingest/feedback",
                json={**_REQ, "input_tokens": 10, "output_tokens": 20, "latency_ms": 5},
            )
        assert resp.status_code == 202
        mock_metrics.assert_called_once()
        assert mock_metrics.call_args.kwargs["input_tokens"] == 10

    def test_enqueue_failure_returns_500(self, client, monkeypatch):
        monkeypatch.setattr("config.ENABLE_FEEDBACK_LOOP", True)
        with patch(
            "app.processor.jobs.feedback_ingest.enqueue_feedback_ingest_job",
            side_effect=RuntimeError("redis down"),
        ):
            resp = client.post("/ingest/feedback", json=_REQ)
        assert resp.status_code == 500
