# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Self-RAG runs under a wall-clock ceiling.

F112: ``agent_query`` is bounded by ``budget_seconds``, but Self-RAG ran
after it with no ceiling of its own, so a slow claim extraction or refined
retrieval could hold the request open indefinitely. These tests stall the
claim extractor (the LLM boundary) and drive the real ``maybe_self_rag`` and
``agent_query_full``, and the smart-mode branch of ``/agent/query``.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import core.agents.hallucination as hallucination
from core.agents import query_agent
from core.agents.self_rag import maybe_self_rag

# An unbounded Self-RAG outlives the deadline and fails the test; the stall
# still ends on its own so a failing run does not hang.
_TEST_DEADLINE_S = 3.0
_STALL_S = 5.0


async def _stalled_extract_claims(_text: str) -> tuple[list[str], str]:
    await asyncio.sleep(_STALL_S)
    return [], "stall_finished"


@pytest.fixture(autouse=True)
def _stall_claim_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hallucination, "extract_claims", _stalled_extract_claims)


def _retrieval_result() -> dict[str, Any]:
    return {
        "context": "kept context",
        "sources": [{"relevance": 0.9}],
        "confidence": 0.9,
        "total_results": 1,
        "results": [{"artifact_id": "a1", "chunk_index": 0, "relevance": 0.9}],
    }


async def test_self_rag_stops_at_the_configured_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "AGENT_QUERY_BUDGET_SECONDS", 0.05)
    original = _retrieval_result()

    result = await asyncio.wait_for(
        maybe_self_rag(
            original, "an answer to validate", True,
            chroma_client=None, neo4j_driver=None, redis_client=None,
        ),
        timeout=_TEST_DEADLINE_S,
    )

    assert result["self_rag"]["status"] == "timed_out"
    assert result["results"] == original["results"]
    assert result["context"] == original["context"]


async def test_per_request_budget_overrides_the_configured_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "AGENT_QUERY_BUDGET_SECONDS", 60.0)

    result = await asyncio.wait_for(
        maybe_self_rag(
            _retrieval_result(), "an answer to validate", True,
            chroma_client=None, neo4j_driver=None, redis_client=None,
            budget_seconds=0.05,
        ),
        timeout=_TEST_DEADLINE_S,
    )

    assert result["self_rag"]["status"] == "timed_out"


async def test_agent_query_full_passes_its_budget_to_self_rag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "AGENT_QUERY_BUDGET_SECONDS", 60.0)

    result = await asyncio.wait_for(
        query_agent.agent_query_full(
            "anything",
            kb_enabled=False,
            external_augmentation=False,
            response_text="an answer to validate",
            enable_self_rag=True,
            budget_seconds=0.05,
        ),
        timeout=_TEST_DEADLINE_S,
    )

    assert result["self_rag"]["status"] == "timed_out"
    assert result["strategy"] == "conversation_only"


class _UnreachableRedis:
    """Fails every call the way a down Redis does, without opening a socket."""

    def __getattr__(self, name: str) -> Any:
        def _fail(*_a: Any, **_kw: Any) -> Any:
            raise ConnectionError(f"redis unreachable ({name})")

        return _fail


def test_smart_mode_passes_the_request_budget_to_self_rag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import deps
    from app.agents import retrieval_orchestrator
    from app.routers import agents

    async def _orchestrated(**_kw: Any) -> dict[str, Any]:
        return {**_retrieval_result(), "domains_searched": ["general"]}

    monkeypatch.setattr(config, "AGENT_QUERY_BUDGET_SECONDS", 60.0)
    monkeypatch.setattr(retrieval_orchestrator, "orchestrated_query", _orchestrated)
    monkeypatch.setattr(deps, "_redis", _UnreachableRedis())
    for handle in ("get_chroma", "get_neo4j", "get_graph_store", "get_redis"):
        monkeypatch.setattr(agents, handle, lambda: None)
    app = FastAPI()
    app.include_router(agents.router)

    res = TestClient(app).post(
        "/agent/query",
        json={
            "query": "anything",
            "rag_mode": "smart",
            "skip_cache": True,
            "response_text": "an answer to validate",
            "enable_self_rag": True,
            "budget_seconds": 1,
        },
    )

    assert res.status_code == 200, res.text
    assert res.json()["self_rag"]["status"] == "timed_out"
