# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Nothing re-creates a forgotten conversation's data: jobs, memory extraction,
verification reports, the hall: cache and the feedback route all skip it."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

FORGOTTEN = "c-forgotten"
LIVE = "c-live"


@pytest.fixture(autouse=True)
def _forgotten(monkeypatch):
    monkeypatch.setattr("core.forget.registry.is_forgotten", lambda kind, id: id == FORGOTTEN)


async def _noop_progress(_p):
    return None


def test_feedback_ingest_job_skips_without_ingesting():
    from app.processor.jobs.feedback_ingest import FeedbackIngestJob
    job = FeedbackIngestJob(user_message="q", assistant_response="a", model="m", conversation_id=FORGOTTEN)
    with patch("app.services.ingestion.ingest_content") as ingest:
        result = asyncio.run(job.run(_noop_progress))
    ingest.assert_not_called()
    assert result.metadata == {"conversation_id": FORGOTTEN, "skipped": "forgotten"}


def test_session_summary_job_skips(monkeypatch):
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    from app.processor.jobs.session_summary import SessionSummaryJob
    job = SessionSummaryJob(FORGOTTEN)
    with patch.object(SessionSummaryJob, "_run_pipeline") as pipeline:
        result = asyncio.run(job.run(_noop_progress))
    pipeline.assert_not_called()
    assert result.metadata["skipped"] == "forgotten"


def test_memory_funnel_refuses_a_forgotten_conversation():
    from app.agents import memory
    with patch.object(memory, "_core_extract_and_store_memories", new_callable=AsyncMock) as core:
        out = asyncio.run(memory.extract_and_store_memories("text", FORGOTTEN))
    core.assert_not_called()
    assert out == {"status": "skipped", "reason": "forgotten"}


def test_memory_funnel_still_extracts_for_a_live_conversation():
    from app.agents import memory
    with patch.object(
        memory, "_core_extract_and_store_memories", new_callable=AsyncMock,
        return_value={"status": "success"},
    ) as core:
        out = asyncio.run(memory.extract_and_store_memories("text", LIVE))
    core.assert_awaited_once()
    assert out == {"status": "success"}


def test_verification_report_is_not_saved_for_a_forgotten_conversation():
    from app.db.neo4j.artifacts import save_verification_report
    driver = MagicMock()
    assert save_verification_report(
        driver, conversation_id=FORGOTTEN, claims=[], overall_score=0.0,
        verified=0, unverified=0, uncertain=0, total=0,
    ) is None
    driver.session.assert_not_called()


def test_hall_cache_write_is_skipped_for_a_forgotten_conversation():
    from core.agents.hallucination import streaming
    assert streaming._may_persist_report(FORGOTTEN) is False
    assert streaming._may_persist_report(LIVE) is True


# ── The hall: write sites themselves, driven through the real pipelines ──────

_RESPONSE = (
    "The sky is blue during the daytime because molecules in the atmosphere "
    "scatter blue light more strongly than red light across the sky overhead."
)
_CLAIM = "The sky is blue during the daytime."


class _CaptureRedis:
    def __init__(self):
        self.setex_keys: list[str] = []

    def setex(self, key, ttl, payload):
        self.setex_keys.append(key)

    def get(self, key):
        return None

    def setnx(self, *a, **k):
        return True

    def expire(self, *a, **k):
        return True

    def rpush(self, *a, **k):
        return 1

    def zadd(self, *a, **k):
        return 1

    def ltrim(self, *a, **k):
        return True


def _patch_pipeline(monkeypatch):
    from core.agents.hallucination import streaming

    async def _extract(response_text, user_query=None):
        return [_CLAIM], "heuristic"

    async def _verify(*a, **k):
        return {
            "status": "verified", "confidence": 0.9, "similarity": 0.9,
            "nli_entailment": 0.9, "nli_contradiction": 0.05, "memory_source": True,
            "source_filename": "doc.md", "reason": "supported by KB",
            "claim_type": "factual", "verification_method": "kb",
            "verification_model": "test-model",
        }

    monkeypatch.setattr(streaming, "extract_claims", _extract)
    monkeypatch.setattr(streaming, "_extract_claims_heuristic", lambda text: [_CLAIM])
    monkeypatch.setattr(streaming, "_resolve_pronouns_heuristic", lambda claims, *a, **k: claims)
    monkeypatch.setattr(streaming, "verify_claim", _verify)


@pytest.mark.parametrize("cid, expect_write", [(FORGOTTEN, False), (LIVE, True)])
async def test_check_hallucinations_writes_hall_only_for_a_live_conversation(monkeypatch, cid, expect_write):
    from core.agents.hallucination import streaming
    _patch_pipeline(monkeypatch)
    redis = _CaptureRedis()
    await streaming.check_hallucinations(
        response_text=_RESPONSE, conversation_id=cid,
        chroma_client=MagicMock(), neo4j_driver=MagicMock(), redis_client=redis,
    )
    hall = f"{streaming.REDIS_HALLUCINATION_PREFIX}{cid}"
    assert (hall in redis.setex_keys) is expect_write


@pytest.mark.parametrize("cid, expect_write", [(FORGOTTEN, False), (LIVE, True)])
async def test_verify_stream_writes_hall_only_for_a_live_conversation(monkeypatch, cid, expect_write):
    from core.agents.hallucination import streaming
    _patch_pipeline(monkeypatch)
    redis = _CaptureRedis()
    async for _event in streaming.verify_response_streaming(
        response_text=_RESPONSE, conversation_id=cid,
        chroma_client=MagicMock(), neo4j_driver=MagicMock(), redis_client=redis,
    ):
        pass
    hall = f"{streaming.REDIS_HALLUCINATION_PREFIX}{cid}"
    assert (hall in redis.setex_keys) is expect_write


async def test_verify_stream_reports_not_persisted_when_the_save_is_refused(monkeypatch):
    from core.agents.hallucination import streaming
    _patch_pipeline(monkeypatch)
    events = [
        e async for e in streaming.verify_response_streaming(
            response_text=_RESPONSE, conversation_id=FORGOTTEN,
            chroma_client=MagicMock(), neo4j_driver=MagicMock(), redis_client=_CaptureRedis(),
            save_report_fn=lambda **kwargs: None,
        )
    ]
    persisted = [e for e in events if e.get("type") == "persisted"]
    assert persisted == [{"type": "persisted", "success": False}]


# ── POST /ingest/feedback ─────────────────────────────────────────────────────

@pytest.fixture
def feedback_client(monkeypatch):
    import config
    from app.routers import ingestion

    monkeypatch.setattr(config, "ENABLE_FEEDBACK_LOOP", True, raising=False)
    app = FastAPI()
    app.include_router(ingestion.router)
    with patch.object(ingestion, "get_redis", return_value=MagicMock()):
        yield TestClient(app, raise_server_exceptions=False)


def test_feedback_turn_for_a_forgotten_conversation_is_neither_metered_nor_queued(feedback_client):
    with (
        patch("core.utils.cache.log_conversation_metrics") as metrics,
        patch("app.processor.jobs.feedback_ingest.enqueue_feedback_ingest_job") as enqueue,
    ):
        res = feedback_client.post("/ingest/feedback", json={
            "conversation_id": FORGOTTEN, "user_message": "q", "assistant_response": "a",
            "input_tokens": 5, "output_tokens": 7,
        })
    assert res.status_code == 200
    assert res.json() == {"status": "skipped", "reason": "forgotten"}
    metrics.assert_not_called()
    enqueue.assert_not_called()


def test_feedback_sentiment_for_a_forgotten_conversation_is_not_recorded(feedback_client):
    with patch("core.utils.cache.log_conversation_sentiment") as sentiment:
        res = feedback_client.post("/ingest/feedback", json={
            "conversation_id": FORGOTTEN, "message_id": "m1", "sentiment": "up",
        })
    assert res.json() == {"status": "skipped", "reason": "forgotten"}
    sentiment.assert_not_called()


def test_feedback_turn_for_a_live_conversation_is_still_queued(feedback_client):
    with patch("app.processor.jobs.feedback_ingest.enqueue_feedback_ingest_job", return_value="j1") as enqueue:
        res = feedback_client.post("/ingest/feedback", json={
            "conversation_id": LIVE, "user_message": "q", "assistant_response": "a",
        })
    assert res.status_code == 202
    enqueue.assert_called_once()


# ── Verification report routes: a refused save is reported as not saved ──────

def _quiet_agents_router(monkeypatch):
    import fakeredis
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fakeredis.FakeRedis(decode_responses=True))
    for getter in ("get_chroma", "get_neo4j", "get_redis"):
        monkeypatch.setattr(f"app.routers.agents.{getter}", lambda: MagicMock(), raising=False)


async def test_verification_save_route_reports_skipped_for_a_forgotten_conversation(monkeypatch):
    _quiet_agents_router(monkeypatch)
    from app.routers.agents import SaveVerificationRequest, save_verification_report
    resp = await save_verification_report(SaveVerificationRequest(
        conversation_id=FORGOTTEN, claims=[{"text": _CLAIM, "status": "verified"}],
        overall_score=0.9, verified=1, total=1,
    ))
    assert resp == {"status": "skipped", "report_id": None}


async def test_hallucination_endpoint_does_not_claim_a_refused_auto_persist(monkeypatch):
    _quiet_agents_router(monkeypatch)

    async def _check(**kwargs):
        return {
            "conversation_id": kwargs["conversation_id"], "skipped": False,
            "claims": [{"text": _CLAIM, "status": "verified"}],
            "summary": {"total": 1, "verified": 1, "unverified": 0, "uncertain": 0,
                        "overall_confidence": 0.9},
        }

    monkeypatch.setattr("core.agents.hallucination.check_hallucinations", _check)
    from app.routers.agents import HallucinationCheckRequest, hallucination_check_endpoint
    result = await hallucination_check_endpoint(
        HallucinationCheckRequest(response_text=_RESPONSE, conversation_id=FORGOTTEN)
    )
    assert result["persisted"] is False



@pytest.mark.parametrize("cid, promoted", [(FORGOTTEN, 0), (LIVE, 1)])
def test_verified_fact_promotion_creates_memories_only_for_a_live_conversation(cid, promoted):
    from core.agents.verified_memory import promote_verified_facts
    neo4j = MagicMock()
    neo4j.session.return_value.__enter__ = MagicMock(return_value=MagicMock())
    neo4j.session.return_value.__exit__ = MagicMock(return_value=False)
    create = MagicMock(return_value="mem-1")
    claim = {
        "claim": "The Eiffel Tower is in Paris", "status": "verified", "similarity": 0.94,
        "confidence": 0.94, "claim_type": "factual", "nli_entailment": 0.91,
        "source_artifact_id": "artifact-abc123", "source_filename": "paris.md",
        "verification_method": "kb_nli",
    }
    with patch("core.agents.memory.detect_memory_conflict", new_callable=AsyncMock, return_value=[]):
        counts = asyncio.run(promote_verified_facts(
            {"conversation_id": cid, "claims": [claim]}, MagicMock(), neo4j, create_memory_fn=create,
        ))
    assert counts["promoted"] == promoted
    assert create.call_count == promoted
    assert counts.get("skipped_forgotten", 0) == 1 - promoted


# ── Verification report reads: a trashed conversation's claims are unreachable ─

def _hall_redis(cid: str):
    import json

    import fakeredis

    from core.agents.hallucination import REDIS_HALLUCINATION_PREFIX
    redis = fakeredis.FakeRedis(decode_responses=True)
    redis.setex(f"{REDIS_HALLUCINATION_PREFIX}{cid}", 600, json.dumps({"claims": [{"text": "secret claim"}]}))
    return redis


def test_hall_report_is_not_read_for_a_forgotten_conversation():
    from core.agents.hallucination import get_hallucination_report
    assert get_hallucination_report(_hall_redis(FORGOTTEN), FORGOTTEN) is None
    assert get_hallucination_report(_hall_redis(LIVE), LIVE) == {"claims": [{"text": "secret claim"}]}


def test_verification_report_is_not_read_for_a_forgotten_conversation():
    from app.db.neo4j.artifacts import get_verification_report
    driver = MagicMock()
    assert get_verification_report(driver, FORGOTTEN) is None
    driver.session.assert_not_called()


async def test_hall_report_route_answers_404_for_a_forgotten_conversation(monkeypatch):
    from fastapi import HTTPException
    _quiet_agents_router(monkeypatch)
    monkeypatch.setattr("app.routers.agents.get_redis", lambda: _hall_redis(FORGOTTEN))
    from app.routers.agents import hallucination_report_endpoint
    with pytest.raises(HTTPException) as exc:
        await hallucination_report_endpoint(FORGOTTEN)
    assert exc.value.status_code == 404


async def test_verification_report_route_answers_404_for_a_forgotten_conversation(monkeypatch):
    from fastapi import HTTPException
    _quiet_agents_router(monkeypatch)
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value.run.return_value.single.return_value = {
        "id": "r1", "conversation_id": FORGOTTEN, "claims": '[{"text": "secret claim"}]',
        "overall_score": 0.9, "verified": 1, "agreed": 0, "unverified": 0, "uncertain": 0,
        "total": 1, "created_at": "2026-10-07",
    }
    monkeypatch.setattr("app.routers.agents.get_neo4j", lambda: driver)
    from app.routers.agents import get_verification_report
    with pytest.raises(HTTPException) as exc:
        await get_verification_report(FORGOTTEN)
    assert exc.value.status_code == 404


async def test_claim_feedback_writes_nothing_for_a_forgotten_conversation(monkeypatch):
    from fastapi import HTTPException

    from core.agents.hallucination import REDIS_HALLUCINATION_PREFIX
    _quiet_agents_router(monkeypatch)
    redis = _hall_redis(FORGOTTEN)
    key = f"{REDIS_HALLUCINATION_PREFIX}{FORGOTTEN}"
    before = redis.get(key)
    monkeypatch.setattr("app.routers.agents.get_redis", lambda: redis)
    from app.routers.agents import ClaimFeedbackRequest, claim_feedback_endpoint
    with pytest.raises(HTTPException) as exc:
        await claim_feedback_endpoint(ClaimFeedbackRequest(conversation_id=FORGOTTEN, claim_index=0, correct=True))
    assert exc.value.status_code == 404
    assert redis.get(key) == before
    assert redis.ttl(key) <= 600


async def test_claim_feedback_never_recreates_a_report_purged_after_it_was_read(monkeypatch):
    """The read and the write are not atomic: a purge in between must win."""
    import fakeredis

    from core.agents.hallucination import REDIS_HALLUCINATION_PREFIX
    _quiet_agents_router(monkeypatch)
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.routers.agents.get_redis", lambda: redis)
    monkeypatch.setattr(
        "core.agents.hallucination.get_hallucination_report",
        lambda r, cid: {"claims": [{"text": "secret claim"}], "model": "m"},
    )
    monkeypatch.setattr("core.utils.cache.log_claim_feedback", lambda *a, **k: None)
    from app.routers.agents import ClaimFeedbackRequest, claim_feedback_endpoint
    await claim_feedback_endpoint(ClaimFeedbackRequest(conversation_id=LIVE, claim_index=0, correct=True))
    assert redis.get(f"{REDIS_HALLUCINATION_PREFIX}{LIVE}") is None


async def test_claim_feedback_still_updates_a_live_report(monkeypatch):
    import json

    from core.agents.hallucination import REDIS_HALLUCINATION_PREFIX
    _quiet_agents_router(monkeypatch)
    redis = _hall_redis(LIVE)
    monkeypatch.setattr("app.routers.agents.get_redis", lambda: redis)
    monkeypatch.setattr("core.utils.cache.log_claim_feedback", lambda *a, **k: None)
    from app.routers.agents import ClaimFeedbackRequest, claim_feedback_endpoint
    assert await claim_feedback_endpoint(
        ClaimFeedbackRequest(conversation_id=LIVE, claim_index=0, correct=False),
    ) == {"status": "ok"}
    stored = json.loads(redis.get(f"{REDIS_HALLUCINATION_PREFIX}{LIVE}"))
    assert stored["claims"][0]["user_feedback"] == "incorrect"
