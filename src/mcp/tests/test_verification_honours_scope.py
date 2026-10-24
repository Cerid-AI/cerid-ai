# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Verification answers to the request's policy (audit F83-F86, F92-F94).

It judged every claim against the whole knowledge base and the owner's
memories, whoever asked and at any Private Mode level: a restricted consumer
calling /sdk/v1/hallucination had its claims checked against domains its wall
keeps it out of. Only the evidence stores are faked; the scope, the evidence
stage and the transport are real.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.agents.guarded_retrieval import verification_scope
from core.agents.hallucination import verification
from core.agents.request_context import RequestContext


class _Doors:
    def __init__(self) -> None:
        self.kb_domains: list[list[str]] = []
        self.memory_reads = 0

    async def kb(self, query: str, domains: list[str] | None = None, **_: Any) -> list[dict]:
        self.kb_domains.append(list(domains or []))
        return []

    async def memory(self, *_a: Any, **_k: Any) -> list[dict]:
        self.memory_reads += 1
        return []


@pytest.fixture
def doors(monkeypatch) -> _Doors:
    d = _Doors()
    monkeypatch.setattr("core.agents.query_agent.lightweight_kb_query", d.kb)
    monkeypatch.setattr(verification, "_query_memories", d.memory)
    return d


async def _gather() -> None:
    await verification._gather_kb_evidence(
        "The Eiffel Tower is 330 metres tall.", object(), response_context=None,
    )


@pytest.mark.asyncio
async def test_private_mode_l2_reads_no_knowledge_and_no_memory(doors):
    with verification_scope(RequestContext(private_level=2)):
        await _gather()

    assert doors.kb_domains == [] and doors.memory_reads == 0


@pytest.mark.asyncio
async def test_a_walled_consumer_verifies_against_its_own_domains_only(doors):
    ctx = RequestContext(client_id="cerid-finance", allowed_domains=("finance", "inbox"), strict_domains=True)

    with verification_scope(ctx):
        await _gather()

    assert len(doors.kb_domains) == 1 and set(doors.kb_domains[0]) <= {"finance", "inbox"}
    assert doors.memory_reads == 0  # its strict scope does not name conversations


@pytest.mark.asyncio
async def test_the_owner_still_verifies_against_everything(doors):
    with verification_scope(RequestContext()):
        await _gather()

    assert len(doors.kb_domains) == 1 and len(doors.kb_domains[0]) > 2
    assert "conversations" not in doors.kb_domains[0]
    assert doors.memory_reads == 1


@pytest.mark.asyncio
async def test_the_scope_ends_with_the_request(doors):
    with verification_scope(RequestContext(private_level=2)):
        pass
    await _gather()

    assert doors.kb_domains and doors.memory_reads == 1


@pytest.mark.asyncio
async def test_the_sdk_route_resolves_the_calling_consumer(doors, monkeypatch):
    from app.routers.agents import HallucinationCheckRequest, hallucination_check_endpoint

    async def one_claim(*_a, **_k):
        return ["The Eiffel Tower is 330 metres tall."], "test"

    monkeypatch.setattr("core.agents.hallucination.streaming.extract_claims", one_claim)
    monkeypatch.setattr("config.ALLOW_CLOUD_EGRESS_WHEN_LOCAL", False)
    monkeypatch.setattr("app.services.request_policy.get_private_mode_level", lambda: 0)
    for getter in ("get_chroma", "get_neo4j", "get_redis"):
        monkeypatch.setattr(f"app.routers.agents.{getter}", lambda: None)

    await hallucination_check_endpoint(
        HallucinationCheckRequest(response_text="The Eiffel Tower is 330 metres tall.", conversation_id="c", persist=False),
        SimpleNamespace(headers={"x-client-id": "cerid-finance"}),
    )

    assert doors.kb_domains and all(set(d) <= {"finance", "inbox"} for d in doors.kb_domains)
    assert doors.memory_reads == 0
