# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The shared, policy-enforcing retrieval seam (E1 Phase 1).

Every transport that answers from the KB or episodic memory MUST route through
:func:`guarded_agent_query_full` / :func:`guarded_recall_memories` instead of
calling ``agent_query_full`` / ``recall_memories`` directly. The guard takes a
required :class:`~core.agents.request_context.RequestContext` (an omitted
context is a ``TypeError`` at call sites, not a silent policy grant) and:

1. enforces Private-Mode "skip KB" (level >= 2) by returning a bypass envelope /
   empty recall BEFORE any retrieval, and
2. applies the resolved consumer domain isolation + per-request directives from
   the context, so a transport cannot forget to pass ``allowed_domains`` /
   ``skip_cache`` / ``metadata_filter`` / ``budget_seconds``.

Deviation note (vs the plan's literal shape): enforcement lives in this thin
shared wrapper rather than inside ``agent_query_full`` itself, so the large
retrieval function keeps a stable signature and its many existing callers are
untouched. The intent — one shared enforcement point every transport inherits,
omitted context = error — is preserved.
"""
from __future__ import annotations

from typing import Any

from core.agents.request_context import RequestContext


def kb_bypassed_envelope() -> dict[str, Any]:
    """The empty KB envelope returned when Private Mode blocks retrieval.

    Matches the shape the canonical ``/agent/query`` handler returns at L2 so
    every transport is byte-compatible with the existing contract.
    """
    # E1 CR-032/062: no write-only kb_bypassed flag — empty envelope is the signal
    # (degraded_reason / Private Mode is the UI-facing path).
    return {
        "context": "",
        "sources": [],
        "results": [],
        "domains_searched": [],
        "total_results": 0,
        "confidence": 0.0,
    }


async def guarded_agent_query_full(
    *,
    request_context: RequestContext,
    query: str,
    domains: list[str] | None = None,
    top_k: int = 10,
    use_reranking: bool = True,
    conversation_messages: list[dict[str, str]] | None = None,
    chroma_client: Any | None = None,
    redis_client: Any | None = None,
    neo4j_driver: Any | None = None,
    graph_store: Any | None = None,
    model: str | None = None,
    exclude_packs: bool = False,
    kb_enabled: bool = True,
    external_augmentation: bool = True,
    response_text: str | None = None,
    enable_self_rag: bool | None = None,
    debug_timing: bool = False,
) -> dict[str, Any]:
    """Policy-enforcing wrapper over ``agent_query_full``.

    Private-Mode L2 short-circuits to :func:`kb_bypassed_envelope`. Otherwise the
    consumer isolation + per-request directives are taken from ``request_context``
    (never from loose kwargs the caller might forget).
    """
    if request_context.blocks_kb:
        return kb_bypassed_envelope()

    from core.agents.query_agent import agent_query_full

    return await agent_query_full(
        query=query,
        domains=domains,
        top_k=top_k,
        use_reranking=use_reranking,
        conversation_messages=conversation_messages,
        chroma_client=chroma_client,
        redis_client=redis_client,
        neo4j_driver=neo4j_driver,
        graph_store=graph_store,
        debug_timing=debug_timing,
        allowed_domains=request_context.allowed_domains_list(),
        strict_domains=request_context.strict_domains,
        model=model,
        skip_cache=request_context.skip_cache,
        metadata_filter=request_context.metadata_filter,
        exclude_packs=exclude_packs,
        kb_enabled=kb_enabled,
        external_augmentation=external_augmentation,
        response_text=response_text,
        enable_self_rag=enable_self_rag,
        budget_seconds=request_context.budget_seconds,
    )


async def guarded_recall_memories(
    *,
    request_context: RequestContext,
    query: str,
    chroma_client: Any | None = None,
    neo4j_driver: Any | None = None,
    top_k: int = 10,
    min_score: float | None = None,
) -> list[dict]:
    """Policy-enforcing wrapper over ``recall_memories``.

    Private-Mode L2 treats episodic memory as protected content (the L2+
    contract strips ``<memory`` blocks at the generation boundary), so recall
    returns empty before any vector search.
    """
    if request_context.blocks_kb:
        return []

    from core.agents.memory import recall_memories

    return await recall_memories(
        query=query,
        chroma_client=chroma_client,
        neo4j_driver=neo4j_driver,
        top_k=top_k,
        min_score=min_score,
    )
