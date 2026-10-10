# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for once-per-session summarization (bi-temporal memory plan Phase E).

Two layers:
- ``core.agents.session_summary.summarize_session`` — the pure, store-free,
  DI-driven single-LLM-call consolidator (output shaping, deterministic guards,
  budget bound, stage breadcrumb).
- ``app.processor.jobs.session_summary.SessionSummaryJob`` — the queued job
  wrapper (dark flag, idempotent skip, ingest with memory_scope metadata).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agents.session_summary import (
    default_llm_caller,
    summarize_session,
)

# ---------------------------------------------------------------------------
# core.agents.session_summary.summarize_session
# ---------------------------------------------------------------------------

_GOOD_TURNS = (
    "User switched from almond to oat milk on 2026-06-01.\n\n---\n\n"
    "Decided to migrate the trading stack to Python 3.12.\n\n---\n\n"
    "Prefers dark mode across all dashboards. Open thread: still need the "
    "SLO budget confirmed before flipping the decomposition flag."
)


def _json_caller(payload: dict) -> AsyncMock:
    """An llm_caller stub returning a fixed JSON object string."""
    return AsyncMock(return_value=json.dumps(payload))


@pytest.mark.asyncio
async def test_summarize_session_shapes_output():
    caller = _json_caller(
        {
            "content": "Session covered a milk switch, a Python 3.12 migration "
            "decision, and a dark-mode preference; SLO budget still open.",
            "summary": "Milk switch + py3.12 migration + dark-mode pref",
            "event_date": "2026-06-01",
        }
    )
    result = await summarize_session(
        turns_text=_GOOD_TURNS,
        conversation_id="conv-1",
        session_date="2026-06-01",
        llm_caller=caller,
    )
    assert result is not None
    assert result["memory_type"] == "conversational"
    assert result["event_date"] == "2026-06-01"
    assert "migration" in result["content"]
    assert result["summary"]
    caller.assert_awaited_once()


@pytest.mark.asyncio
async def test_summarize_session_empty_input_returns_none():
    caller = _json_caller({"content": "x", "summary": "x", "event_date": None})
    result = await summarize_session(
        turns_text="   too short   ",
        conversation_id="conv-1",
        session_date="2026-06-01",
        llm_caller=caller,
    )
    assert result is None
    caller.assert_not_awaited()  # no LLM call on a deterministic short-circuit


@pytest.mark.asyncio
async def test_summarize_session_event_date_falls_back_to_session_date():
    caller = _json_caller(
        {"content": "A consolidated recap of the session.", "summary": "s", "event_date": "null"}
    )
    result = await summarize_session(
        turns_text=_GOOD_TURNS,
        conversation_id="conv-1",
        session_date="2026-07-04",
        llm_caller=caller,
    )
    assert result is not None
    assert result["event_date"] == "2026-07-04"


@pytest.mark.asyncio
async def test_summarize_session_budget_bounds_a_stalled_call(monkeypatch):
    """A generation exceeding SESSION_SUMMARY_LLM_BUDGET_S is abandoned (None),
    proving the budget bound is applied around the injected caller."""
    monkeypatch.setattr(
        "core.agents.session_summary.SESSION_SUMMARY_LLM_BUDGET_S", 0.05
    )

    async def _slow(_messages):
        await asyncio.sleep(0.5)
        return "{}"

    result = await summarize_session(
        turns_text=_GOOD_TURNS,
        conversation_id="conv-1",
        session_date="2026-06-01",
        llm_caller=_slow,
    )
    assert result is None


@pytest.mark.asyncio
async def test_summarize_session_caps_input_length(monkeypatch):
    monkeypatch.setattr(
        "core.agents.session_summary.SESSION_SUMMARY_MIN_INPUT_CHARS", 10
    )
    monkeypatch.setattr(
        "core.agents.session_summary.SESSION_SUMMARY_MAX_INPUT_CHARS", 40
    )
    long_text = ("A" * 60) + "ZZZUNIQUE_TAIL_MARKER"
    caller = _json_caller({"content": "c", "summary": "s", "event_date": None})

    await summarize_session(
        turns_text=long_text,
        conversation_id="conv-1",
        session_date="2026-06-01",
        llm_caller=caller,
    )
    prompt = caller.await_args.args[0][0]["content"]
    # The tail beyond the 40-char cap must not reach the prompt.
    assert "ZZZUNIQUE_TAIL_MARKER" not in prompt


@pytest.mark.asyncio
async def test_summarize_session_llm_failure_returns_none():
    caller = AsyncMock(side_effect=RuntimeError("bifrost down"))
    result = await summarize_session(
        turns_text=_GOOD_TURNS,
        conversation_id="conv-1",
        session_date="2026-06-01",
        llm_caller=caller,
    )
    assert result is None


@pytest.mark.asyncio
async def test_summarize_session_non_dict_json_returns_none():
    caller = AsyncMock(return_value="[]")
    result = await summarize_session(
        turns_text=_GOOD_TURNS,
        conversation_id="conv-1",
        session_date="2026-06-01",
        llm_caller=caller,
    )
    assert result is None


@pytest.mark.asyncio
async def test_default_llm_caller_passes_stage():
    with patch(
        "core.utils.internal_llm.call_internal_llm",
        new_callable=AsyncMock,
        return_value="{}",
    ) as mock_llm:
        await default_llm_caller([{"role": "user", "content": "hi"}])
    mock_llm.assert_awaited_once()
    assert mock_llm.await_args.kwargs["stage"] == "session_summary"


# ---------------------------------------------------------------------------
# app.processor.jobs.session_summary.SessionSummaryJob
# ---------------------------------------------------------------------------

from app.processor.jobs.session_summary import (  # noqa: E402
    SessionSummaryJob,
    enqueue_session_summary_job,
)
from core.processor.priority import Priority  # noqa: E402


async def _noop_progress(_pct: float) -> None:
    return None


def _fake_driver(single_row=None):
    """A Neo4j driver whose one session yields a run() result with the given
    ``.single()`` row. Returns (driver, session_mock) so tests can inspect the
    Cypher calls."""
    driver = MagicMock()
    sess = MagicMock()
    result = MagicMock()
    result.single.return_value = single_row
    sess.run.return_value = result
    ctx = MagicMock()
    ctx.__enter__.return_value = sess
    ctx.__exit__.return_value = False
    driver.session.return_value = ctx
    return driver, sess


_SUMMARY = {
    "content": "Consolidated session recap.",
    "summary": "recap",
    "memory_type": "conversational",
    "event_date": "2026-06-01",
}
_TURNS = [
    {"chunk_id": "t1", "content": "turn one", "created_at": "2026-06-01T10:00:00+00:00", "valid_from": "2026-06-01"},
    {"chunk_id": "t2", "content": "turn two", "created_at": "2026-06-01T10:05:00+00:00", "valid_from": "2026-06-01"},
]


def test_job_registered_in_default_registry():
    from app.processor.worker import build_default_registry

    registry = build_default_registry()
    assert registry.get("session_summary") is SessionSummaryJob


def test_job_is_low_priority_and_free():
    job = SessionSummaryJob(conversation_id="conv-1")
    assert job.priority is Priority.LOW
    assert float(job.estimate_cost().estimated_usd) == 0.0


def test_record_payload_round_trips_for_worker_dispatch():
    """Worker re-instantiates as job_class(**record.payload)."""
    job = SessionSummaryJob(conversation_id="conv-1", tenant_id="default")
    record = job.new_record(payload={"conversation_id": "conv-1", "tenant_id": "default"})
    clone = SessionSummaryJob(**record.payload)
    assert clone.job_type == "session_summary"
    assert clone._conversation_id == "conv-1"


@pytest.mark.asyncio
async def test_run_no_op_when_flag_off(monkeypatch):
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", False)
    with patch("app.services.ingestion.ingest_content") as mock_ingest:
        result = await SessionSummaryJob(conversation_id="conv-1").run(_noop_progress)
    assert result.metadata["skipped"] == "feature_disabled"
    mock_ingest.assert_not_called()


@pytest.mark.asyncio
async def test_run_skips_when_already_summarized(monkeypatch):
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    driver, _sess = _fake_driver(single_row={"ids": ["existing-summary"], "stale": False})

    with (
        patch("app.deps.get_neo4j", return_value=driver),
        patch("app.deps.get_chroma", return_value=MagicMock()),
        patch(
            "core.agents.session_summary.summarize_session", new_callable=AsyncMock
        ) as mock_sum,
    ):
        result = await SessionSummaryJob(conversation_id="conv-1").run(_noop_progress)

    assert result.metadata["skipped"] == "already_summarized"
    mock_sum.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_skips_when_no_memories(monkeypatch):
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    driver, _sess = _fake_driver(single_row=None)

    with (
        patch("app.deps.get_neo4j", return_value=driver),
        patch("app.deps.get_chroma", return_value=MagicMock()),
        patch(
            "app.processor.jobs.session_summary._fetch_session_turns",
            return_value=[],
        ),
    ):
        result = await SessionSummaryJob(conversation_id="conv-1").run(_noop_progress)

    assert result.metadata["skipped"] == "no_memories"


@pytest.mark.asyncio
async def test_run_skips_when_summary_empty(monkeypatch):
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    driver, _sess = _fake_driver(single_row=None)

    with (
        patch("app.deps.get_neo4j", return_value=driver),
        patch("app.deps.get_chroma", return_value=MagicMock()),
        patch(
            "app.processor.jobs.session_summary._fetch_session_turns",
            return_value=_TURNS,
        ),
        patch(
            "core.agents.session_summary.summarize_session",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("app.services.ingestion.ingest_content") as mock_ingest,
    ):
        result = await SessionSummaryJob(conversation_id="conv-1").run(_noop_progress)

    assert result.metadata["skipped"] == "summary_empty"
    mock_ingest.assert_not_called()


@pytest.mark.asyncio
async def test_run_ingests_with_memory_scope_metadata(monkeypatch):
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    driver, sess = _fake_driver(single_row=None)  # no existing summary

    with (
        patch("app.deps.get_neo4j", return_value=driver),
        patch("app.deps.get_chroma", return_value=MagicMock()),
        patch(
            "app.processor.jobs.session_summary._fetch_session_turns",
            return_value=_TURNS,
        ),
        patch(
            "core.agents.session_summary.summarize_session",
            new_callable=AsyncMock,
            return_value=_SUMMARY,
        ),
        patch(
            "app.services.ingestion.ingest_content",
            return_value={"status": "success", "artifact_id": "art:sum1"},
        ) as mock_ingest,
    ):
        result = await SessionSummaryJob(conversation_id="conv-1").run(_noop_progress)

    assert result.metadata["summarized"] is True
    assert result.metadata["artifact_id"] == "art:sum1"

    # Ingested into the conversations domain with the distinguishing metadata.
    args, kwargs = mock_ingest.call_args
    assert args[0] == _SUMMARY["content"]
    assert args[1] == "conversations"
    meta = kwargs["metadata"]
    assert meta["memory_scope"] == "session_summary"
    assert meta["conversation_id"] == "conv-1"
    assert meta["memory_type"] == "conversational"
    assert meta["valid_from"] == "2026-06-01"  # from event_date via resolve_valid_from
    assert meta["valid_to"] == ""  # OPEN_INTERVAL

    # The EXTRACTED_FROM + memory_scope marker was written to the node.
    assert any(
        "SET a.memory_scope" in c.args[0] for c in sess.run.call_args_list
    )


def test_enqueue_session_summary_job_dedups_via_if_absent():
    with patch(
        "app.db.redis.processor_queue.enqueue_job_if_absent",
        return_value="job-1",
    ) as mock_enq:
        job_id = enqueue_session_summary_job("conv-9", redis_client=MagicMock())
    assert job_id == "job-1"
    _job, kwargs = mock_enq.call_args
    assert kwargs["payload"] == {"conversation_id": "conv-9", "tenant_id": "default"}


# ---- a summary whose memories changed (forget phase 5, spec §7) ----

@pytest.mark.asyncio
async def test_a_stale_summary_is_written_again_and_the_old_one_erased(monkeypatch):
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    driver, sess = _fake_driver(single_row={"ids": ["art:old"], "stale": True})
    erased: list[Any] = []
    monkeypatch.setattr("app.services.forget.engine.forget_available", lambda: True)
    monkeypatch.setattr(
        "app.services.forget.engine.forget_permanently",
        lambda subjects, requested_by: erased.append(([s.id for s in subjects], requested_by)) or {},
    )
    with (
        patch("app.deps.get_neo4j", return_value=driver),
        patch("app.deps.get_chroma", return_value=MagicMock()),
        patch("app.processor.jobs.session_summary._fetch_session_turns", return_value=_TURNS),
        patch("core.agents.session_summary.summarize_session", new_callable=AsyncMock, return_value=_SUMMARY),
        patch("app.services.ingestion.ingest_content",
              return_value={"status": "success", "artifact_id": "art:new"}),
    ):
        result = await SessionSummaryJob(conversation_id="conv-1").run(_noop_progress)

    assert result.metadata["artifact_id"] == "art:new" and result.metadata["retired"] == 1
    assert erased == [(["art:old"], "derived")]
    marks = [c.args[0] for c in sess.run.call_args_list if "SET a.memory_scope" in c.args[0]]
    assert marks and "REMOVE a.summary_stale" in marks[0]


@pytest.mark.asyncio
async def test_the_same_text_again_keeps_its_summary(monkeypatch):
    """Content-addressed: an unchanged summary comes back under its own id."""
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    driver, _sess = _fake_driver(single_row={"ids": ["art:same"], "stale": True})
    forget = MagicMock()
    monkeypatch.setattr("app.services.forget.engine.forget_permanently", forget)
    with (
        patch("app.deps.get_neo4j", return_value=driver),
        patch("app.deps.get_chroma", return_value=MagicMock()),
        patch("app.processor.jobs.session_summary._fetch_session_turns", return_value=_TURNS),
        patch("core.agents.session_summary.summarize_session", new_callable=AsyncMock, return_value=_SUMMARY),
        patch("app.services.ingestion.ingest_content",
              return_value={"status": "duplicate", "artifact_id": "art:same"}),
    ):
        result = await SessionSummaryJob(conversation_id="conv-1").run(_noop_progress)
    assert result.metadata["retired"] == 0
    forget.assert_not_called()


@pytest.mark.asyncio
async def test_a_stale_summary_with_nothing_left_to_summarize_is_erased(monkeypatch):
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    driver, _sess = _fake_driver(single_row={"ids": ["art:old"], "stale": True})
    erased: list[Any] = []
    monkeypatch.setattr("app.services.forget.engine.forget_available", lambda: True)
    monkeypatch.setattr(
        "app.services.forget.engine.forget_permanently",
        lambda subjects, requested_by: erased.append([s.id for s in subjects]) or {},
    )
    with (
        patch("app.deps.get_neo4j", return_value=driver),
        patch("app.deps.get_chroma", return_value=MagicMock()),
        patch("app.processor.jobs.session_summary._fetch_session_turns", return_value=[]),
    ):
        result = await SessionSummaryJob(conversation_id="conv-1").run(_noop_progress)
    assert result.metadata["skipped"] == "no_memories" and erased == [["art:old"]]


def test_only_current_memories_are_summarized(monkeypatch):
    from core.forget import registry as forget_registry
    from tests.helpers.fake_chroma import FakeChromaClient, FakeChromaCollection

    col = FakeChromaCollection("conversations")
    rows = {
        "now": {"memory_type": "fact", "valid_to": ""},
        "before": {"memory_type": "fact", "valid_to": "2026-06-01", "superseded_by": "now", "version_closed": 1},
        "trashed": {"memory_type": "fact", "valid_to": ""},
        "summary": {"memory_type": "fact", "memory_scope": "session_summary"},
        "child": {"memory_type": "fact", "valid_to": "", "parent_chunk_id": "parent_c"},
    }
    for cid, meta in rows.items():
        col.upsert(ids=[f"{cid}_c"], documents=[f"text {cid}"], embeddings=[[1.0, 0.0]],
                   metadatas=[{"artifact_id": cid, "conversation_id": "conv-1", **meta}])
    gone = {"artifact": frozenset({"trashed"}), "chunk": frozenset({"parent_c"})}
    monkeypatch.setattr(forget_registry, "forgotten_ids", lambda kind: gone.get(kind, frozenset()))
    monkeypatch.setattr("core.forget.read_filter.forgotten_ids", lambda kind: gone.get(kind, frozenset()))
    monkeypatch.setattr("config.collection_name", lambda domain: "conversations")
    from app.processor.jobs.session_summary import _fetch_session_turns

    turns = _fetch_session_turns(FakeChromaClient([col]), "conv-1")
    assert [t["content"] for t in turns] == ["text now"]


@pytest.mark.asyncio
async def test_a_memory_forgotten_while_summarizing_takes_the_new_summary_with_it(monkeypatch):
    """The forget's refresh collapses onto the running job, which read the memory first."""
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    driver, _sess = _fake_driver(single_row=None)
    erased: list[Any] = []
    monkeypatch.setattr("app.services.forget.engine.forget_available", lambda: True)
    monkeypatch.setattr(
        "app.services.forget.engine.forget_permanently",
        lambda subjects, requested_by: erased.append([s.id for s in subjects]) or {},
    )
    with (
        patch("app.deps.get_neo4j", return_value=driver),
        patch("app.deps.get_chroma", return_value=MagicMock()),
        patch("app.processor.jobs.session_summary._fetch_session_turns", side_effect=[_TURNS, _TURNS[:1]]),
        patch("core.agents.session_summary.summarize_session", new_callable=AsyncMock, return_value=_SUMMARY),
        patch("app.services.ingestion.ingest_content",
              return_value={"status": "success", "artifact_id": "art:new"}),
    ):
        result = await SessionSummaryJob(conversation_id="conv-1").run(_noop_progress)
    assert result.metadata["skipped"] == "inputs_changed" and erased == [["art:new"]]


@pytest.mark.asyncio
async def test_a_store_that_cannot_be_read_erases_nothing(monkeypatch):
    """None is "could not read": a stale summary is not taken for one with nothing left."""
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    driver, _sess = _fake_driver(single_row={"ids": ["art:old"], "stale": True})
    forget = MagicMock()
    monkeypatch.setattr("app.services.forget.engine.forget_permanently", forget)
    with (
        patch("app.deps.get_neo4j", return_value=driver),
        patch("app.deps.get_chroma", return_value=MagicMock()),
        patch("app.processor.jobs.session_summary._fetch_session_turns", return_value=None),
        pytest.raises(RuntimeError),
    ):
        await SessionSummaryJob(conversation_id="conv-1").run(_noop_progress)
    forget.assert_not_called()


@pytest.mark.asyncio
async def test_a_failed_summary_keeps_the_stale_one(monkeypatch):
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    driver, _sess = _fake_driver(single_row={"ids": ["art:old"], "stale": True})
    forget = MagicMock()
    monkeypatch.setattr("app.services.forget.engine.forget_permanently", forget)
    with (
        patch("app.deps.get_neo4j", return_value=driver),
        patch("app.deps.get_chroma", return_value=MagicMock()),
        patch("app.processor.jobs.session_summary._fetch_session_turns", return_value=_TURNS),
        patch("core.agents.session_summary.summarize_session", new_callable=AsyncMock, return_value=None),
    ):
        result = await SessionSummaryJob(conversation_id="conv-1").run(_noop_progress)
    assert result.metadata["skipped"] == "summary_empty"
    forget.assert_not_called()


def test_a_collection_that_cannot_be_read_is_not_an_empty_one():
    from app.processor.jobs.session_summary import _fetch_session_turns

    broken = MagicMock()
    broken.get_or_create_collection.side_effect = RuntimeError("chroma down")
    assert _fetch_session_turns(broken, "conv-1") is None
    assert _fetch_session_turns(None, "conv-1") is None
