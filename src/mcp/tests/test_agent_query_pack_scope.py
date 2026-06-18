# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pack-scoped retrieval contract for ``/agent/query``.

Eval finding 06-test-rag (2026-05-26): the wizard's demo-queries panel
asked "What is the standard deduction for a single filer?" with the IRS
Publications pack just installed, and the retrieval returned a Roth IRA
chunk from the pre-seeded ``personal`` namespace instead of any IRS Pub
content. Root cause: the demo path issued ``/agent/query`` with no
metadata filter — the hybrid retriever returned globally top-N chunks.

Fix contract: when the caller passes
``metadata_filter={"pack_id": <id>}`` the retrieval layer scopes to
that pack. This file pins the wiring end-to-end via the router so a
future refactor that drops the field plumb-through is caught at PR time.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """FastAPI TestClient with stubbed external deps."""
    from app.routers import agents

    fake_chroma = MagicMock()
    fake_neo4j = MagicMock()
    fake_redis = MagicMock()

    app = FastAPI()
    app.include_router(agents.router)

    with (
        patch.object(agents, "get_chroma", return_value=fake_chroma),
        patch.object(agents, "get_neo4j", return_value=fake_neo4j),
        patch.object(agents, "get_graph_store", return_value=MagicMock()),
        patch.object(agents, "get_redis", return_value=fake_redis),
    ):
        yield TestClient(app, raise_server_exceptions=False)


class TestPackScopedQuery:
    """``metadata_filter={"pack_id": ...}`` reaches ``core.agents.query_agent``."""

    @pytest.mark.asyncio
    async def test_pack_id_filter_reaches_query_agent(self, client):
        """The router forwards ``metadata_filter`` verbatim to ``agent_query``.

        Wizard demo path sets ``metadata_filter={"pack_id": pack.id}`` so the
        retrieval is pinned to the just-installed pack's chunks. The contract
        below makes the wiring auditable.
        """
        captured: dict = {}

        async def fake_agent_query(**kwargs):
            captured.update(kwargs)
            return {
                "context": "",
                "sources": [],
                "confidence": 0.0,
                "domains_searched": [],
                "total_results": 0,
                "results": [],
            }

        with patch(
            "core.agents.query_agent.agent_query",
            new=AsyncMock(side_effect=fake_agent_query),
        ):
            res = client.post(
                "/agent/query",
                json={
                    "query": "What is the standard deduction for a single filer?",
                    "domains": ["personal"],
                    "top_k": 3,
                    "use_reranking": False,
                    "skip_cache": True,
                    "metadata_filter": {"pack_id": "irs-publications-curated"},
                },
            )

        assert res.status_code == 200, res.text
        # The router MUST pass the caller's metadata_filter through unchanged.
        assert captured.get("metadata_filter") == {
            "pack_id": "irs-publications-curated"
        }, (
            "demo-rag scoping bug returns: metadata_filter was not forwarded "
            f"to retrieval. Got: {captured.get('metadata_filter')!r}"
        )
        # skip_cache must also reach retrieval — wizard installs are a textbook
        # fresh-data scenario and the semantic cache could otherwise serve a
        # pre-pack hit.
        assert captured.get("skip_cache") is True

    @pytest.mark.asyncio
    async def test_exclude_packs_reaches_query_agent(self, client):
        """Slice 7.3: the router forwards ``exclude_packs`` to ``agent_query``
        so the personal-first pack drop is applied (default False when omitted)."""
        captured: dict = {}

        async def fake_agent_query(**kwargs):
            captured.update(kwargs)
            return {
                "context": "", "sources": [], "confidence": 0.0,
                "domains_searched": [], "total_results": 0, "results": [],
            }

        with patch(
            "core.agents.query_agent.agent_query",
            new=AsyncMock(side_effect=fake_agent_query),
        ):
            res = client.post(
                "/agent/query",
                json={
                    "query": "summarize my notes",
                    "exclude_packs": True,
                    "skip_cache": True,
                    "use_reranking": False,
                },
            )
        assert res.status_code == 200, res.text
        assert captured.get("exclude_packs") is True

    @pytest.mark.asyncio
    async def test_exclude_packs_defaults_false(self, client):
        """Omitting exclude_packs keeps packs in scope (default False)."""
        captured: dict = {}

        async def fake_agent_query(**kwargs):
            captured.update(kwargs)
            return {
                "context": "", "sources": [], "confidence": 0.0,
                "domains_searched": [], "total_results": 0, "results": [],
            }

        with patch(
            "core.agents.query_agent.agent_query",
            new=AsyncMock(side_effect=fake_agent_query),
        ):
            res = client.post(
                "/agent/query",
                json={"query": "anything", "skip_cache": True, "use_reranking": False},
            )
        assert res.status_code == 200, res.text
        assert captured.get("exclude_packs") is False
