# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for the /agent/* router shape invariants.

Two systemic invariants land here:

  D. **Every endpoint consumed by an external SDK returns an object,
     never a top-level array.** Top-level arrays break naive
     ``body.get("result", body)`` parsers and are indistinguishable from
     "actual error" in clients that ``.get()`` on the response. Enforced
     via ``response_model=`` on the handler + a contract test that asserts
     ``isinstance(body, dict)`` for both empty and non-empty responses.

  E. **Required-but-validatable identifiers carry their requirement at
     the schema level.** A ``conversation_id`` that defaults to ``""``
     and 422s in the handler is a guaranteed-422 trap that costs every
     consumer a request slot before they learn the field is required.
     Enforced via ``Field(..., min_length=1)`` so Pydantic rejects up-front
     and the constraint surfaces in the OpenAPI spec (the
     ``sdk-openapi-drift`` gate keeps it stable across releases).

These tests run in the default ``test`` job and gate every PR.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """FastAPI TestClient wrapping the agents router with stubbed dependencies."""
    from app.routers import agents

    fake_chroma = MagicMock()
    fake_neo4j = MagicMock()
    fake_redis = MagicMock()

    app = FastAPI()
    app.include_router(agents.router)

    with (
        patch.object(agents, "get_chroma", return_value=fake_chroma),
        patch.object(agents, "get_neo4j", return_value=fake_neo4j),
        patch.object(agents, "get_redis", return_value=fake_redis),
    ):
        yield TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# D. Object-envelope contract for /agent/memory/recall
# ---------------------------------------------------------------------------


class TestMemoryRecallEnvelope:
    """The handler must return ``{memories, total}`` for every code path —
    empty result, populated result, and the graceful-degradation error
    branch. Top-level arrays are forbidden by the systemic D invariant."""

    @pytest.mark.asyncio
    async def test_empty_recall_returns_object(self, client):
        with patch(
            "core.agents.memory.recall_memories",
            new=AsyncMock(return_value=[]),
        ):
            res = client.post("/agent/memory/recall", json={"query": "test"})
        assert res.status_code == 200
        body = res.json()
        assert isinstance(body, dict), (
            f"D invariant: expected dict envelope, got {type(body).__name__}"
        )
        assert body == {"memories": [], "total": 0}

    @pytest.mark.asyncio
    async def test_populated_recall_returns_object(self, client):
        results = [
            {"content": "memory 1", "adjusted_score": 0.9},
            {"content": "memory 2", "adjusted_score": 0.7},
        ]
        with patch(
            "core.agents.memory.recall_memories",
            new=AsyncMock(return_value=results),
        ):
            res = client.post(
                "/agent/memory/recall",
                json={"query": "test", "min_score": 0.5},
            )
        assert res.status_code == 200
        body = res.json()
        assert isinstance(body, dict)
        assert body["total"] == 2
        assert len(body["memories"]) == 2

    @pytest.mark.asyncio
    async def test_min_score_filter_returns_object(self, client):
        results = [
            {"content": "high", "adjusted_score": 0.9},
            {"content": "low", "adjusted_score": 0.2},
        ]
        with patch(
            "core.agents.memory.recall_memories",
            new=AsyncMock(return_value=results),
        ):
            res = client.post(
                "/agent/memory/recall",
                json={"query": "test", "min_score": 0.5},
            )
        body = res.json()
        assert body["total"] == 1
        assert body["memories"][0]["content"] == "high"

    @pytest.mark.asyncio
    async def test_internal_error_returns_object_not_500(self, client):
        """Graceful degradation must preserve the dict envelope — a list
        return on the error path would re-introduce the bug for callers
        that hit the failure branch."""
        with patch(
            "core.agents.memory.recall_memories",
            new=AsyncMock(side_effect=RuntimeError("neo4j is sad")),
        ):
            res = client.post("/agent/memory/recall", json={"query": "test"})
        assert res.status_code == 200
        body = res.json()
        assert isinstance(body, dict)
        assert body == {"memories": [], "total": 0}


# ---------------------------------------------------------------------------
# E. Schema-level required check for conversation_id
# ---------------------------------------------------------------------------


class TestConversationIdRequired:
    """Pydantic validation must reject empty ``conversation_id`` before the
    handler runs. Surfaces the requirement in the OpenAPI spec; the
    ``sdk-openapi-drift`` gate keeps the constraint stable across releases."""

    def test_memory_extract_rejects_empty_conversation_id(self, client):
        res = client.post(
            "/agent/memory/extract",
            json={"response_text": "x" * 300, "conversation_id": ""},
        )
        assert res.status_code == 422
        # Pydantic error mentions the field
        body = res.json()
        assert any(
            "conversation_id" in str(err.get("loc", []))
            for err in body.get("detail", [])
        ), f"Expected conversation_id error in detail; got: {body}"

    def test_memory_extract_rejects_missing_conversation_id(self, client):
        res = client.post(
            "/agent/memory/extract",
            json={"response_text": "x" * 300},
        )
        assert res.status_code == 422

    def test_hallucination_rejects_empty_conversation_id(self, client):
        res = client.post(
            "/agent/hallucination",
            json={"response_text": "x" * 300, "conversation_id": ""},
        )
        assert res.status_code == 422

    def test_hallucination_rejects_missing_conversation_id(self, client):
        res = client.post(
            "/agent/hallucination",
            json={"response_text": "x" * 300},
        )
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_memory_extract_accepts_non_empty_conversation_id(self, client):
        """Non-empty values reach the handler. We don't exercise the full
        memory pipeline here — that's covered in test_memory.py — just
        prove the schema doesn't false-reject valid input."""
        with patch(
            "app.agents.memory.extract_and_store_memories",
            new=AsyncMock(return_value={
                "conversation_id": "valid-conv",
                "timestamp": "2026-05-08T00:00:00Z",
                "memories_extracted": 0,
                "memories_stored": 0,
                "skipped_duplicates": 0,
                "results": [],
            }),
        ):
            res = client.post(
                "/agent/memory/extract",
                json={"response_text": "x" * 300, "conversation_id": "valid-conv"},
            )
        assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# OpenAPI spec assertions — the constraint must surface for SDK consumers
# ---------------------------------------------------------------------------


class TestOpenAPIConstraintExposure:
    """SDK consumers read the OpenAPI spec to generate clients. The
    ``min_length=1`` constraint must appear in the schema so generated
    clients know up-front rather than hitting 422s in production."""

    def test_min_length_appears_in_openapi(self, client):
        res = client.get("/openapi.json")
        assert res.status_code == 200
        spec = res.json()
        schemas = spec["components"]["schemas"]
        for model_name in ("HallucinationCheckRequest", "MemoryExtractionRequest"):
            field = schemas[model_name]["properties"]["conversation_id"]
            assert field.get("minLength") == 1, (
                f"{model_name}.conversation_id missing minLength=1: {field}"
            )

    def test_hallucination_mode_enum_in_openapi(self, client):
        """``mode`` field exposes the fast | thorough enum so SDK
        consumers know they can opt into the cheap path. Same shape
        guarantee the openapi-drift gate checks across releases."""
        spec = client.get("/openapi.json").json()
        schemas = spec["components"]["schemas"]
        # Pydantic emits enums either inline or via $ref to a Components-level
        # schema; accept either form.
        req = schemas["HallucinationCheckRequest"]["properties"]["mode"]
        if "$ref" in req:
            ref_name = req["$ref"].split("/")[-1]
            enum_schema = schemas[ref_name]
        else:
            enum_schema = req
        # The enum vals must include both choices
        enum_vals = enum_schema.get("enum", [])
        assert "fast" in enum_vals and "thorough" in enum_vals, (
            f"HallucinationMode missing fast/thorough: {enum_schema}"
        )


# ---------------------------------------------------------------------------
# B. Fast-mode hallucination check (Workstream A interface issue B)
# ---------------------------------------------------------------------------


class TestHallucinationFastMode:
    """Class invariant: post-fact annotation handlers must expose a fast
    path. ``mode=fast`` skips cross-model NLI entirely, returns claims
    with status='uncertain' and ``nli_skipped=true`` flag, completes in
    extraction-only time. Trading-agent's reflection hooks (currently
    wrapped in ``asyncio.wait_for(2.0)``) gain a server-side contract
    they can rely on instead of enforcing the ceiling client-side."""

    @pytest.mark.asyncio
    async def test_fast_mode_skips_nli_and_returns_uncertain_claims(self, client):
        """``mode=fast`` runs only the extraction stage. Claims must come
        back with ``status='uncertain'`` + ``verification_skipped=true``
        and the response must carry ``nli_skipped=true`` + ``mode='fast'``.
        ``check_hallucinations`` (the heavy NLI path) must NOT be called."""

        async def _fake_extract(text, user_query=None):
            return ["Claim about Mars.", "Claim about caffeine."], "heuristic"

        with (
            patch(
                "core.agents.hallucination.extraction.extract_claims",
                new=AsyncMock(side_effect=_fake_extract),
            ),
            patch(
                "core.agents.hallucination.check_hallucinations",
                new=AsyncMock(side_effect=AssertionError(
                    "fast mode must NOT call check_hallucinations"
                )),
            ),
        ):
            res = client.post(
                "/agent/hallucination",
                json={
                    "response_text": "x" * 300,
                    "conversation_id": "fast-test",
                    "mode": "fast",
                },
            )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["mode"] == "fast"
        assert body["nli_skipped"] is True
        assert body["persisted"] is False
        assert body["summary"]["total"] == 2
        assert body["summary"]["uncertain"] == 2
        assert body["summary"]["verified"] == 0
        assert body["summary"]["unverified"] == 0
        for claim in body["claims"]:
            assert claim["status"] == "uncertain"
            assert claim["verification_skipped"] is True

    @pytest.mark.asyncio
    async def test_thorough_mode_is_default_and_calls_nli_pipeline(self, client):
        """Default behaviour preserved — without ``mode``, the handler
        runs the full ``check_hallucinations`` pipeline. Caller gets
        ``mode='thorough'`` in the response so they know which path ran."""

        async def _fake_check(**_kwargs):
            return {
                "conversation_id": "thorough-test",
                "timestamp": "2026-05-08T00:00:00Z",
                "skipped": False,
                "claims": [{"text": "claim", "status": "verified", "confidence": 0.9}],
                "summary": {"total": 1, "verified": 1, "unverified": 0, "uncertain": 0},
            }

        with (
            patch(
                "core.agents.hallucination.check_hallucinations",
                new=_fake_check,
            ),
            patch(
                "app.db.neo4j.memory.create_memory_node", new=MagicMock(),
            ),
            patch(
                "app.db.neo4j.artifacts.save_verification_report", new=MagicMock(),
            ),
        ):
            res = client.post(
                "/agent/hallucination",
                json={
                    "response_text": "x" * 300,
                    "conversation_id": "thorough-test",
                    "persist": False,  # avoid the Neo4j path in this test
                },
            )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["mode"] == "thorough"
        # nli_skipped is not set on the thorough path (default False / absent)
        assert body.get("nli_skipped") is not True

    @pytest.mark.asyncio
    async def test_fast_mode_with_no_extracted_claims_returns_empty(self, client):
        """Edge case: extraction returns empty — fast mode must still
        emit an object envelope (matches systemic D invariant), no NLI
        calls, no claims."""

        async def _fake_extract(text, user_query=None):
            return [], "heuristic"

        with patch(
            "core.agents.hallucination.extraction.extract_claims",
            new=AsyncMock(side_effect=_fake_extract),
        ):
            res = client.post(
                "/agent/hallucination",
                json={
                    "response_text": "x" * 300,
                    "conversation_id": "fast-empty",
                    "mode": "fast",
                },
            )
        body = res.json()
        assert isinstance(body, dict)
        assert body["mode"] == "fast"
        assert body["nli_skipped"] is True
        assert body["claims"] == []
        assert body["summary"]["total"] == 0

    @pytest.mark.asyncio
    async def test_invalid_mode_value_rejected_by_schema(self, client):
        res = client.post(
            "/agent/hallucination",
            json={
                "response_text": "x" * 300,
                "conversation_id": "bad-mode",
                "mode": "lightning",  # not in the enum
            },
        )
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# F. truth_audit feature gate — Task 2.6b
# ---------------------------------------------------------------------------


class TestTruthAuditGate:
    """The 7 verification/hallucination endpoints are gated behind the
    community-tier ``truth_audit`` flag (default True). This makes the tier
    contract explicit for enterprise builds that may flip it off, without
    changing behavior for current (community/pro) users."""

    @pytest.fixture
    def gated_client(self):
        """Same stubbed-dependency app as ``client``, but with the CeridError
        handler registered so a raised ``FeatureGateError`` renders as 403
        instead of a bare 500 — ``require_feature`` raises ``FeatureGateError``
        directly, not ``HTTPException``."""
        from app.error_handlers import register_cerid_error_handler
        from app.routers import agents

        fake_chroma = MagicMock()
        fake_neo4j = MagicMock()
        fake_redis = MagicMock()

        app = FastAPI()
        app.include_router(agents.router)
        register_cerid_error_handler(app)

        with (
            patch.object(agents, "get_chroma", return_value=fake_chroma),
            patch.object(agents, "get_neo4j", return_value=fake_neo4j),
            patch.object(agents, "get_redis", return_value=fake_redis),
        ):
            yield TestClient(app, raise_server_exceptions=False)

    def test_default_tier_does_not_gate_hallucination_check(self, client):
        """``truth_audit`` defaults True — the gate must be a no-op for
        current users. Uses fast mode to avoid exercising the full NLI
        pipeline; only the gate's pass-through behavior is under test."""
        res = client.post(
            "/agent/hallucination",
            json={
                "response_text": "x" * 300,
                "conversation_id": "gate-default",
                "mode": "fast",
            },
        )
        assert res.status_code != 403
        assert res.status_code == 200, res.text

    @pytest.mark.parametrize(
        ("method", "path", "json_body"),
        [
            ("post", "/agent/hallucination",
             {"response_text": "x" * 300, "conversation_id": "c1"}),
            ("get", "/agent/hallucination/c1", None),
            ("post", "/agent/hallucination/feedback",
             {"conversation_id": "c1", "claim_index": 0, "correct": True}),
            ("post", "/agent/verify-stream",
             {"response_text": "x" * 300, "conversation_id": "c1"}),
            ("post", "/verification/save",
             {"conversation_id": "c1", "claims": [], "overall_score": 0.5}),
            ("get", "/verification/c1", None),
            ("post", "/agent/audit", {}),
        ],
        ids=[
            "hallucination-check",
            "hallucination-report",
            "hallucination-feedback",
            "verify-stream",
            "verification-save",
            "verification-get",
            "audit",
        ],
    )
    def test_forced_off_returns_403_for_all_gated_endpoints(
        self, gated_client, method, path, json_body,
    ):
        from config.features import FEATURE_FLAGS

        with patch.dict(FEATURE_FLAGS, {"truth_audit": False}):
            call = getattr(gated_client, method)
            res = call(path, json=json_body) if json_body is not None else call(path)

        assert res.status_code == 403, res.text
        body = res.json()
        assert body["error_code"] == "FEATURE_GATE_ERROR"
        assert "message" in body
