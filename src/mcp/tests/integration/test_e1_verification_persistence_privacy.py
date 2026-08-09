# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-1d verifiability harness — VERIFICATION-PERSISTENCE PRIVACY probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 1.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-018, CR-086).

The finding (S9, "two-store divergence"): verification reports persist to Redis
``hall:{cid}`` (7-day TTL) AND to Neo4j ``:VerificationReport`` at *every*
private-mode level, while the sibling verified-fact memory promotion is already
gated at L1 (``_verified_memory_fn`` nulls ``create_memory_fn``). So during a
Private-Mode L1+ session — where conversation saves return ``{saved: None}`` —
the response's verbatim claim sentences + KB source snippets are still written
durably and are retrievable afterwards by conversation id. One store honors the
privacy tier; its twin silently does not.

Private-Mode **L1** ("skip saves & sync") is the contract boundary at which all
durable server-side saves of conversation-derived data stop. This probe asserts
verification-report persistence joins that class, on both stores and across
every user-facing transport (REST ``/agent/hallucination`` + ``/agent/verify-
stream`` + ``/verification/save``, MCP ``pkb_check_hallucinations``, A2A
verification — SDK is covered via its delegation to the REST handler).

These are **synthetic** probes — no live stack. The core-mechanism tests drive
the REAL ``check_hallucinations`` / ``verify_response_streaming`` with patched
extraction + a stubbed ``verify_claim`` and a capturing fake Redis; the
transport tests drive the REAL handlers with a global private-mode level set in
a fake Redis and a spy on the core function, asserting the honored
``persist_report`` signal + the router-side Neo4j gate.

RED-then-GREEN: written against today's (pre-fix) code where persistence is
ungated — every gating assertion is RED. The Phase-1d fix (a ``saves_blocked()``
service helper + a ``persist_report`` kwarg threaded from each transport into the
core seam) flips them GREEN, at which point they are live ``@pytest.mark.
preservation`` gates: a regression that re-opens the leak on any store or
transport fails the merge.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest

import core.agents.hallucination.streaming as streaming

PRIVATE_MODE_KEY = "cerid:private_mode:global"

# A response comfortably above HALLUCINATION_MIN_RESPONSE_LENGTH that our patched
# heuristic extractor reduces to exactly one non-current-event claim.
_RESPONSE = (
    "The sky is blue during the daytime because molecules in the atmosphere "
    "scatter blue light more strongly than red light across the sky overhead."
)
_CLAIM = "The sky is blue during the daytime."


class _CaptureRedis:
    """Fake redis capturing hall:{cid} setex payloads; no-ops the metric
    side-channels the pipelines best-effort-write."""

    def __init__(self):
        self.setex_calls: list[tuple[str, str]] = []

    def setex(self, key, ttl, payload):
        self.setex_calls.append((key, payload))

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


def _stub_verified_claim(**overrides):
    result = {
        "status": "verified",
        "confidence": 0.9,
        "similarity": 0.9,
        "nli_entailment": 0.9,
        "nli_contradiction": 0.05,
        "memory_source": True,
        "source_filename": "doc.md",
        "reason": "supported by KB",
        "claim_type": "factual",
        "verification_method": "kb",
        "verification_model": "test-model",
    }
    result.update(overrides)
    return result


def _patch_pipeline(monkeypatch):
    """Reduce the real pipelines to a single deterministic verified claim."""
    monkeypatch.setattr(streaming, "extract_claims",
                        _fake_extract_claims)
    monkeypatch.setattr(streaming, "_extract_claims_heuristic",
                        lambda text: [_CLAIM])
    monkeypatch.setattr(streaming, "_resolve_pronouns_heuristic",
                        lambda claims, *a, **k: claims)

    async def _fake_verify_claim(*a, **k):
        return _stub_verified_claim()

    monkeypatch.setattr(streaming, "verify_claim", _fake_verify_claim)


async def _fake_extract_claims(response_text, user_query=None):
    return [_CLAIM], "heuristic"


def _hall_key(conversation_id: str) -> str:
    return f"{streaming.REDIS_HALLUCINATION_PREFIX}{conversation_id}"


# ---------------------------------------------------------------------------
# Group 1 — CORE MECHANISM: persist_report=False suppresses the Redis write in
# BOTH core functions (and the Neo4j save_report_fn in the streaming path).
# ---------------------------------------------------------------------------

@pytest.mark.preservation
async def test_check_hallucinations_persist_report_false_suppresses_redis(monkeypatch):
    """``check_hallucinations(persist_report=False)`` must NOT write the
    ``hall:{cid}`` report to Redis. RED on HEAD: the param does not exist yet
    (the Redis write at streaming.py:418 is unconditional). Closes CR-086."""
    _patch_pipeline(monkeypatch)
    redis = _CaptureRedis()
    await streaming.check_hallucinations(
        response_text=_RESPONSE,
        conversation_id="e1-hall-private",
        chroma_client=MagicMock(),
        neo4j_driver=MagicMock(),
        redis_client=redis,
        persist_report=False,
    )
    assert redis.setex_calls == [], (
        "check_hallucinations persisted the report to Redis despite "
        "persist_report=False — private-mode save-gating bypassed (CR-086)"
    )


@pytest.mark.preservation
async def test_check_hallucinations_persists_by_default(monkeypatch):
    """Green anchor: with the default (persist_report=True) the report IS
    written to Redis — proves the probe drives the real persist path and the
    gate does not over-block normal (non-private) operation."""
    _patch_pipeline(monkeypatch)
    redis = _CaptureRedis()
    await streaming.check_hallucinations(
        response_text=_RESPONSE,
        conversation_id="e1-hall-open",
        chroma_client=MagicMock(),
        neo4j_driver=MagicMock(),
        redis_client=redis,
    )
    keys = [k for k, _ in redis.setex_calls]
    assert _hall_key("e1-hall-open") in keys, (
        "default check_hallucinations did not persist the report to Redis — "
        f"the probe is not driving the real persist path (setex keys={keys})"
    )


async def _drive_streaming(monkeypatch, *, persist_report=None):
    """Drive the real streaming generator to completion; return
    (redis, captured_report_kwargs)."""
    _patch_pipeline(monkeypatch)
    redis = _CaptureRedis()
    captured_report: dict = {}

    def _save_report_fn(**kwargs):
        captured_report.update(kwargs)

    kwargs = dict(
        response_text=_RESPONSE,
        conversation_id="e1-stream-priv",
        chroma_client=MagicMock(),
        neo4j_driver=MagicMock(),
        redis_client=redis,
        save_report_fn=_save_report_fn,
    )
    if persist_report is not None:
        kwargs["persist_report"] = persist_report
    async for _event in streaming.verify_response_streaming(**kwargs):
        pass
    return redis, captured_report


@pytest.mark.preservation
async def test_verify_streaming_persist_report_false_suppresses_both_stores(monkeypatch):
    """``verify_response_streaming(persist_report=False)`` must persist to
    NEITHER store: no ``hall:{cid}`` Redis write and no ``save_report_fn`` Neo4j
    invocation. RED on HEAD (the param does not exist). Closes CR-018."""
    redis, captured_report = await _drive_streaming(monkeypatch, persist_report=False)
    assert redis.setex_calls == [], (
        "verify_response_streaming persisted to Redis despite "
        "persist_report=False (CR-018)"
    )
    assert captured_report == {}, (
        "verify_response_streaming invoked save_report_fn (Neo4j) despite "
        "persist_report=False (CR-018)"
    )


@pytest.mark.preservation
async def test_verify_streaming_persists_by_default(monkeypatch):
    """Green anchor: default streaming run persists to both stores — proves the
    harness reaches the persist points and the gate does not over-block."""
    redis, captured_report = await _drive_streaming(monkeypatch, persist_report=None)
    assert redis.setex_calls, "default streaming run did not persist to Redis"
    assert captured_report.get("conversation_id") == "e1-stream-priv", (
        "default streaming run did not invoke save_report_fn (Neo4j)"
    )


# ---------------------------------------------------------------------------
# Group 2 — TRANSPORT WIRING: each user-facing transport honors Private-Mode L1
# by threading persist_report=False into the core seam AND skipping its own
# Neo4j persistence.
# ---------------------------------------------------------------------------

def _set_private(monkeypatch, level: int):
    """Set the global private-mode level in a fake redis that
    ``app.services.private_mode`` reads through."""
    fr = fakeredis.FakeRedis(decode_responses=True)
    if level:
        fr.set(PRIVATE_MODE_KEY, str(level))
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fr)


def _neutral_stores(monkeypatch, *modules):
    for mod_name in modules:
        for getter in ("get_chroma", "get_neo4j", "get_redis", "get_graph_store"):
            monkeypatch.setattr(f"{mod_name}.{getter}", lambda: MagicMock(), raising=False)


class _CheckSpy:
    """Records the kwargs a check_hallucinations call was made with, and returns
    a canned thorough-mode result carrying one claim (so a downstream Neo4j
    auto-persist WOULD fire if it were not gated)."""

    def __init__(self):
        self.kwargs: dict | None = None

    async def __call__(self, **kwargs):
        self.kwargs = kwargs
        return {
            "conversation_id": kwargs.get("conversation_id"),
            "skipped": False,
            "claims": [{"text": _CLAIM, "status": "verified"}],
            "summary": {"total": 1, "verified": 1, "unverified": 0, "uncertain": 0,
                        "overall_confidence": 0.9},
        }


@pytest.mark.preservation
@pytest.mark.parametrize("level,expect_persist", [(0, True), (1, False)])
async def test_hallucination_endpoint_gates_persistence_at_l1(
    monkeypatch, level, expect_persist
):
    """POST /agent/hallucination must thread persist_report and skip its own
    Neo4j auto-persist at Private-Mode L1. RED on HEAD: the handler passes no
    persist_report and always auto-persists. Closes CR-086."""
    _set_private(monkeypatch, level)
    _neutral_stores(monkeypatch, "app.routers.agents")

    check_spy = _CheckSpy()
    monkeypatch.setattr("core.agents.hallucination.check_hallucinations", check_spy)
    neo4j_saves: list = []
    monkeypatch.setattr(
        "app.db.neo4j.artifacts.save_verification_report",
        lambda *a, **k: neo4j_saves.append(k) or "rid",
    )

    from app.routers.agents import HallucinationCheckRequest, hallucination_check_endpoint

    await hallucination_check_endpoint(
        HallucinationCheckRequest(response_text=_RESPONSE, conversation_id="e1-ep-hall")
    )

    threaded = check_spy.kwargs.get("persist_report") if check_spy.kwargs else None
    assert threaded is expect_persist, (
        f"at private level {level}, /agent/hallucination threaded "
        f"persist_report={threaded!r} into check_hallucinations "
        f"(expected {expect_persist!r})"
    )
    if expect_persist:
        assert neo4j_saves, "L0: /agent/hallucination should auto-persist to Neo4j"
    else:
        assert not neo4j_saves, (
            "L1: /agent/hallucination auto-persisted the report to Neo4j despite "
            "private mode (CR-086)"
        )


@pytest.mark.preservation
@pytest.mark.parametrize("level,expect_saved", [(0, True), (1, False)])
async def test_verification_save_endpoint_skips_at_l1(monkeypatch, level, expect_saved):
    """POST /verification/save (caller-supplied report) must not write to Neo4j
    at Private-Mode L1. RED on HEAD: the handler is ungated. Closes CR-086."""
    _set_private(monkeypatch, level)
    _neutral_stores(monkeypatch, "app.routers.agents")
    neo4j_saves: list = []
    monkeypatch.setattr(
        "app.db.neo4j.artifacts.save_verification_report",
        lambda *a, **k: neo4j_saves.append(k) or "rid",
    )

    from app.routers.agents import SaveVerificationRequest, save_verification_report

    resp = await save_verification_report(
        SaveVerificationRequest(
            conversation_id="e1-ep-save",
            claims=[{"text": _CLAIM, "status": "verified"}],
            overall_score=0.9,
            verified=1, unverified=0, uncertain=0, total=1,
        )
    )
    if expect_saved:
        assert neo4j_saves, "L0: /verification/save should persist to Neo4j"
        assert resp["status"] == "saved"
    else:
        assert not neo4j_saves, (
            "L1: /verification/save persisted a report to Neo4j despite private "
            "mode (CR-086)"
        )
        assert resp["status"] == "skipped"
        assert resp["report_id"] is None


class _StreamSpy:
    """Records kwargs and returns a trivial async generator, standing in for
    verify_response_streaming so the SSE endpoint's wiring can be inspected."""

    def __init__(self):
        self.kwargs: dict | None = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self._gen()

    async def _gen(self):
        yield {"type": "complete"}


@pytest.mark.preservation
@pytest.mark.parametrize("level,expect_persist", [(0, True), (1, False)])
async def test_verify_stream_endpoint_threads_persist_report(
    monkeypatch, level, expect_persist
):
    """POST /agent/verify-stream must thread persist_report into
    verify_response_streaming per Private-Mode L1. RED on HEAD: no signal is
    passed. Closes CR-018."""
    _set_private(monkeypatch, level)
    _neutral_stores(monkeypatch, "app.routers.agents")
    stream_spy = _StreamSpy()
    monkeypatch.setattr(
        "core.agents.hallucination.verify_response_streaming", stream_spy
    )

    from app.routers.agents import VerifyStreamRequest, verify_stream_endpoint

    resp = await verify_stream_endpoint(
        VerifyStreamRequest(response_text=_RESPONSE, conversation_id="e1-ep-stream")
    )
    # Drain the StreamingResponse body so the inner event_generator runs.
    async for _chunk in resp.body_iterator:
        pass

    threaded = stream_spy.kwargs.get("persist_report") if stream_spy.kwargs else None
    assert threaded is expect_persist, (
        f"at private level {level}, /agent/verify-stream threaded "
        f"persist_report={threaded!r} into verify_response_streaming "
        f"(expected {expect_persist!r})"
    )


@pytest.mark.preservation
@pytest.mark.parametrize("driver", ["mcp", "a2a"])
@pytest.mark.parametrize("level,expect_persist", [(0, True), (1, False)])
async def test_mcp_and_a2a_verification_gate_at_l1(
    monkeypatch, driver, level, expect_persist
):
    """The MCP ``pkb_check_hallucinations`` tool and the A2A verification skill
    must honor Private-Mode L1 by threading persist_report into the core seam.
    RED on HEAD: both call check_hallucinations with no persist_report. Same
    transport-bypass class as CR-018/086."""
    _set_private(monkeypatch, level)
    _neutral_stores(monkeypatch, "app.tools", "app.routers.a2a")
    check_spy = _CheckSpy()
    monkeypatch.setattr("core.agents.hallucination.check_hallucinations", check_spy)

    if driver == "mcp":
        from app.tools import _dispatch_raw
        await _dispatch_raw(
            "pkb_check_hallucinations",
            {"response_text": _RESPONSE, "conversation_id": "e1-mcp-hall"},
        )
    else:
        from app.routers.a2a import _execute_verification
        await _execute_verification(
            {"response_text": _RESPONSE, "conversation_id": "e1-a2a-hall"}
        )

    threaded = check_spy.kwargs.get("persist_report") if check_spy.kwargs else None
    assert threaded is expect_persist, (
        f"{driver} verification at private level {level} threaded "
        f"persist_report={threaded!r} (expected {expect_persist!r}) — "
        f"transport re-enters the persist path ungated"
    )
