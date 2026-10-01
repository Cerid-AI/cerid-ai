# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""A digest whose model call failed is reported as partial.

When the model call failed the digest came back with every written section
empty, ``skipped=False`` and no reason, and the scheduled job logged
"success" and announced it as ready. The counts in such a digest are real;
the written summary is missing, and nothing said so.

The digest agent, the scheduled job and the processor job run for real. The
fakes sit at the edges: the graph accessor, the model call, the HTTP client
used for the save, the webhook sender and Redis.
"""
from __future__ import annotations

import json
from typing import Any

import fakeredis
import httpx
import pytest

import core.agents.daily_digest as digest
import core.utils.internal_llm as internal_llm


class _Graph:
    def __init__(self, artifacts: list[dict[str, Any]]) -> None:
        self._artifacts = artifacts

    def get_driver(self) -> object:
        return object()

    def list_artifacts(self, _driver: Any, **kw: Any) -> list[dict[str, Any]]:
        if kw.get("domain") == "inbox":
            return []
        return self._artifacts


_ACTIVITY = [
    {"domain": "notes", "filename": "a.md", "summary": "s"},
    {"domain": "notes", "filename": "b.md", "summary": "s"},
    {"domain": "finance", "filename": "c.md", "summary": "s", "quality_score": 0.1},
]


async def _model_down(*_a: Any, **_kw: Any) -> str:
    raise RuntimeError("model unreachable")


async def _model_rambles(*_a: Any, **_kw: Any) -> str:
    return "I could not produce a summary today."


async def _model_answers(*_a: Any, **_kw: Any) -> str:
    return json.dumps({
        "key_threads": [{"title": "Note", "body": "a note"}],
        "action_items": ["file the return"],
    })


@pytest.fixture
def saved(monkeypatch):
    """Accept the save and keep what was posted."""
    import config.features as features

    posted: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"artifact_id": "art:1"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw),
    )
    monkeypatch.setattr(features, "is_feature_enabled", lambda _name: True)
    monkeypatch.setattr(digest, "_graph", _Graph(_ACTIVITY))
    return posted


async def _generate() -> digest.DigestResult:
    return await digest.generate_daily_digest(mcp_base_url="http://cerid.test")


@pytest.mark.asyncio
async def test_a_failed_model_call_makes_the_digest_partial(saved, monkeypatch):
    monkeypatch.setattr(internal_llm, "call_internal_llm", _model_down)

    result = await _generate()

    assert result.partial is True
    assert result.partial_reason == "model_call_failed"
    assert result.skipped is False
    assert (result.artifact_count, result.flagged_count) == (3, 1)
    assert [c["domain"] for c in result.top_categories] == ["notes", "finance"]
    assert result.to_dict()["partial"] is True
    assert result.to_dict()["partial_reason"] == "model_call_failed"


@pytest.mark.asyncio
async def test_an_unreadable_model_answer_makes_the_digest_partial(saved, monkeypatch):
    monkeypatch.setattr(internal_llm, "call_internal_llm", _model_rambles)

    result = await _generate()

    assert result.partial is True
    assert result.partial_reason == "model_response_unreadable"


@pytest.mark.asyncio
async def test_a_summarised_digest_is_not_partial(saved, monkeypatch):
    monkeypatch.setattr(internal_llm, "call_internal_llm", _model_answers)

    result = await _generate()

    assert result.partial is False
    assert result.partial_reason == ""
    assert [s.title for s in result.key_threads] == ["Note"]


@pytest.mark.asyncio
async def test_a_day_with_no_activity_is_not_partial(saved, monkeypatch):
    monkeypatch.setattr(digest, "_graph", _Graph([]))
    monkeypatch.setattr(internal_llm, "call_internal_llm", _model_down)

    result = await _generate()

    assert result.partial is False
    assert result.artifact_count == 0


@pytest.mark.asyncio
async def test_the_saved_digest_says_its_summary_is_missing(saved, monkeypatch):
    monkeypatch.setattr(internal_llm, "call_internal_llm", _model_down)

    await _generate()

    (payload,) = saved
    assert payload["metadata"]["partial"] == "true"
    assert payload["metadata"]["partial_reason"] == "model_call_failed"
    assert "summary is missing" in payload["content"]


@pytest.mark.asyncio
async def test_a_summarised_digest_is_saved_as_complete(saved, monkeypatch):
    monkeypatch.setattr(internal_llm, "call_internal_llm", _model_answers)

    await _generate()

    (payload,) = saved
    assert payload["metadata"]["partial"] == "false"
    assert "partial_reason" not in payload["metadata"]
    assert "summary is missing" not in payload["content"]


# ── the scheduled job ─────────────────────────────────────────────────


@pytest.fixture
def job(saved, monkeypatch):
    import app.deps as deps
    import utils.webhooks as webhooks
    from app import scheduler as sched

    redis = fakeredis.FakeRedis(decode_responses=True)
    fired: list[tuple[str, dict[str, Any]]] = []

    async def _fire_event(event_type: str, payload: dict[str, Any]) -> int:
        fired.append((event_type, payload))
        return 1

    monkeypatch.setenv("CERID_DAILY_DIGEST_ENABLED", "true")
    monkeypatch.setattr(deps, "get_redis", lambda: redis)
    monkeypatch.setattr(sched, "get_redis", lambda: redis)
    monkeypatch.setattr(webhooks, "fire_event", _fire_event)

    def runs() -> list[dict[str, Any]]:
        rows = [json.loads(r) for r in redis.lrange(sched.config.REDIS_SCHEDULER_LOG, 0, -1)]
        return [r for r in rows if r.get("job") == "daily_digest"]

    return runs, fired


@pytest.mark.asyncio
async def test_a_partial_digest_is_logged_as_partial_not_success(job, monkeypatch):
    from app.scheduler import _run_daily_digest

    runs, fired = job
    monkeypatch.setattr(internal_llm, "call_internal_llm", _model_down)

    await _run_daily_digest()

    (run,) = runs()
    assert run["status"] == "partial"
    assert "model_call_failed" in run["detail"]
    assert "3 artifacts" in run["detail"]


@pytest.mark.asyncio
async def test_the_ready_event_says_the_digest_is_partial(job, monkeypatch):
    from app.scheduler import _run_daily_digest

    _runs, fired = job
    monkeypatch.setattr(internal_llm, "call_internal_llm", _model_down)

    await _run_daily_digest()

    ((name, payload),) = fired
    assert name == "digest.ready"
    assert payload["partial"] is True
    assert payload["partial_reason"] == "model_call_failed"
    assert payload["artifact_count"] == 3
    assert payload["summary"] != "Your daily digest is ready."


@pytest.mark.asyncio
async def test_a_summarised_digest_is_still_a_success(job, monkeypatch):
    from app.scheduler import _run_daily_digest

    runs, fired = job
    monkeypatch.setattr(internal_llm, "call_internal_llm", _model_answers)

    await _run_daily_digest()

    assert [r["status"] for r in runs()] == ["success"]
    ((_name, payload),) = fired
    assert payload["partial"] is False
    assert payload["summary"] == "Your daily digest is ready."


@pytest.mark.asyncio
async def test_a_partial_digest_that_was_not_saved_is_still_an_error(job, monkeypatch):
    from app.scheduler import _run_daily_digest

    runs, fired = job
    monkeypatch.setattr(internal_llm, "call_internal_llm", _model_down)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: real_client(
            transport=httpx.MockTransport(lambda _r: httpx.Response(500, json={})), **kw,
        ),
    )

    await _run_daily_digest()

    assert [r["status"] for r in runs()] == ["error"]
    assert fired == []


# ── the run-now processor job ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_run_now_job_reports_a_partial_digest(saved, monkeypatch):
    import app.routers.license as license_router
    from app.processor.jobs.digest_run import DigestRunJob

    monkeypatch.setattr(internal_llm, "call_internal_llm", _model_down)
    monkeypatch.setattr(license_router, "current_license_watermark", lambda: "")

    async def _progress(_pct: float) -> None:
        return None

    result = await DigestRunJob().run(progress_cb=_progress)

    assert result.metadata["partial"] is True
    assert result.metadata["partial_reason"] == "model_call_failed"
    assert result.metadata["artifact_count"] == 3
