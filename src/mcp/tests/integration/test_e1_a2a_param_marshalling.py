# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-1g (part 2) verifiability harness — A2A PARAM-MARSHALLING probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 1.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-033).

``A2ATaskRequest.input`` is an unvalidated dict, and the skill executors read
only a hardcoded subset of it:

- ``_execute_query`` forwarded text/query, domains, top_k, use_reranking — dropping
  conversation_messages, budget_seconds, skip_cache, metadata_filter, exclude_packs,
  strict_domains, model.
- ``_execute_verification`` forwarded only response_text + conversation_id, while
  REST ``/agent/hallucination`` forwards threshold/model/user_query/expert_mode to
  the same ``check_hallucinations``.
- ``A2ATaskRequest.metadata`` is accepted by schema but never persisted, so a
  peer's correlation id cannot be recovered from task state/history.

This probe drives the REAL executors + ``create_task`` with a spy on the seam and
asserts the full param set (and the correlation metadata) survives. RED-then-GREEN;
GREEN → preservation gates.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest


class _KwargSpy:
    def __init__(self, ret):
        self._ret = ret
        self.kwargs: dict | None = None

    async def __call__(self, *args, **kwargs):
        self.kwargs = kwargs
        return self._ret


class _FakePool:
    def acquire(self):
        class _CM:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        return _CM()


def _neutral(monkeypatch):
    fr = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fr)
    monkeypatch.setattr("app.concurrency.KB_POOL", _FakePool())
    for g in ("get_chroma", "get_redis", "get_neo4j", "get_graph_store"):
        monkeypatch.setattr(f"app.routers.a2a.{g}", lambda: MagicMock(), raising=False)


# ---------------------------------------------------------------------------
# CR-033 — the knowledge-query skill must marshal the full query knob set.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
async def test_a2a_query_marshals_full_param_set(monkeypatch):
    """Every REST /agent/query knob a peer supplies must reach the pipeline —
    directly (top_k, use_reranking, conversation_messages, model, exclude_packs)
    and via the request context (skip_cache, metadata_filter, budget_seconds,
    strict_domains). RED on HEAD: most are dropped (CR-033)."""
    _neutral(monkeypatch)
    seam = _KwargSpy({"context": "", "sources": [], "results": []})
    monkeypatch.setattr("core.agents.guarded_retrieval.guarded_agent_query_full", seam)

    from app.routers.a2a import _execute_query
    await _execute_query({
        "query": "who owns the trading book?",
        "domains": ["finance"],
        "top_k": 5,
        "use_reranking": False,
        "conversation_messages": [{"role": "user", "content": "context"}],
        "skip_cache": True,
        "metadata_filter": {"filename": "report.pdf"},
        "exclude_packs": True,
        "strict_domains": True,
        "budget_seconds": 45.0,
        "model": "grok-4",
    })

    k = seam.kwargs
    assert k is not None, "query never reached the guarded seam"
    assert k["top_k"] == 5
    assert k["use_reranking"] is False
    assert k["conversation_messages"] == [{"role": "user", "content": "context"}], \
        "A2A dropped conversation_messages (CR-033)"
    assert k["model"] == "grok-4", "A2A dropped model (CR-033)"
    assert k["exclude_packs"] is True, "A2A dropped exclude_packs (CR-033)"
    ctx = k["request_context"]
    assert ctx.skip_cache is True, "A2A dropped skip_cache (CR-033)"
    assert ctx.metadata_filter == {"filename": "report.pdf"}, "A2A dropped metadata_filter (CR-033)"
    assert ctx.budget_seconds == 45.0, "A2A dropped budget_seconds (CR-033)"
    assert ctx.strict_domains is True, "A2A dropped strict_domains (CR-033)"


# ---------------------------------------------------------------------------
# CR-033 — the verification skill must forward the verifier knobs REST does.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
async def test_a2a_verification_marshals_full_param_set(monkeypatch):
    """threshold / model / user_query / expert_mode must reach check_hallucinations
    on the A2A path, matching REST /agent/hallucination. RED on HEAD: dropped."""
    _neutral(monkeypatch)
    spy = _KwargSpy({"claims": [], "skipped": False, "summary": {}})
    monkeypatch.setattr("core.agents.hallucination.check_hallucinations", spy)

    from app.routers.a2a import _execute_verification
    await _execute_verification({
        "response_text": "The sky is blue during the daytime.",
        "conversation_id": "c1",
        "threshold": 0.8,
        "model": "grok-4",
        "user_query": "why is the sky blue?",
        "expert_mode": True,
    })

    k = spy.kwargs
    assert k is not None, "verification never reached check_hallucinations"
    assert k.get("threshold") == 0.8, "A2A dropped threshold (CR-033)"
    assert k.get("model") == "grok-4", "A2A dropped model (CR-033)"
    assert k.get("user_query") == "why is the sky blue?", "A2A dropped user_query (CR-033)"
    assert k.get("expert_mode") is True, "A2A dropped expert_mode (CR-033)"


# ---------------------------------------------------------------------------
# CR-033 — a peer's correlation metadata must persist on the task.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
async def test_a2a_task_persists_correlation_metadata(monkeypatch):
    """A2ATaskRequest.metadata must be persisted on the task so cross-agent
    correlation ids are recoverable. RED on HEAD: create_task never records it."""
    import app.routers.a2a as a2a_mod

    saved: dict = {}
    monkeypatch.setattr(a2a_mod, "_save_task", lambda t: saved.update({t["id"]: t}))
    monkeypatch.setattr(a2a_mod, "_append_history", lambda *a, **k: None)

    async def _noop_exec(_inp):
        return {"ok": True}

    monkeypatch.setitem(a2a_mod.SKILL_MAP, "knowledge-query", _noop_exec)

    from app.routers.a2a import A2ATaskRequest, create_task
    task = await create_task(A2ATaskRequest(
        skill_id="knowledge-query",
        input={"query": "q"},
        metadata={"trace_id": "abc-123", "peer": "agent-x"},
    ))

    persisted = saved.get(task["id"], {})
    assert task.get("metadata") == {"trace_id": "abc-123", "peer": "agent-x"}, \
        "A2A task dropped correlation metadata — cross-agent tracing broken (CR-033)"
    assert persisted.get("metadata") == {"trace_id": "abc-123", "peer": "agent-x"}, \
        "correlation metadata not persisted to task state (CR-033)"
