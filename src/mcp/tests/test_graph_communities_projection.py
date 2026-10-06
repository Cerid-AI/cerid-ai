# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""``pkb_graph_communities`` projects and reads back without Neo4j's ``id()``.

Neo4j 2026.09 answers every ``id()`` with a deprecation that carries a removal
notice ("This feature is deprecated and will be removed in future versions ...
'id' has been replaced by 'elementId or consider using an application-generated
id'"). GDS cannot take ``elementId()`` strings, so the projection moved to the
Cypher aggregation form, which takes the nodes themselves, and the Louvain
stream maps GDS node ids back to the artifact's own ``id`` property inside the
statement. The earlier ``gds.graph.project.cypher`` call also failed outright
on both 2026.04 and 2026.09 because its node query returned string columns
("Unsupported conversion to GDS Value from Neo4j Value with type `String`").

These drive the tool with a recording session, so no Neo4j is required.
"""
from __future__ import annotations

import re
from unittest.mock import patch

import pytest

pytest.importorskip("app.mcp_tools.graph_tools")

from app.mcp_tools import graph_tools  # noqa: E402

ID_CALL = re.compile(r"\bid\(")


class _Result:
    def __init__(self, rows: list[dict] | None = None, single: dict | None = None):
        self._rows = rows or []
        self._single = single

    def consume(self) -> None:
        return None

    def single(self) -> dict | None:
        return self._single

    def __iter__(self):
        return iter(self._rows)


class _Session:
    """Answers each query in order and records the Cypher it was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, cypher: str, **params) -> _Result:
        self.calls.append((cypher, params))
        if "gds.graph.list" in cypher:
            return _Result(single={"nodeCount": 3})
        if "gds.louvain.stream" in cypher:
            return _Result(rows=[{"community_id": 7, "size": 3, "member_ids": ["x1", "x2", "x3"]}])
        if "a.id IN $ids" in cypher:
            return _Result(rows=[
                {"artifact_id": "x1", "filename": "a.md", "domain": "code"},
                {"artifact_id": "x2", "filename": "b.md", "domain": "code"},
                {"artifact_id": "x3", "filename": "c.md", "domain": "finance"},
            ])
        return _Result()


class _Driver:
    def __init__(self, session: _Session) -> None:
        self._session = session

    def session(self) -> _Session:
        return self._session


async def _run(domain: str = "") -> tuple[dict, _Session]:
    session = _Session()
    with patch.object(graph_tools, "get_neo4j", return_value=_Driver(session)), \
            patch.object(graph_tools.config, "DOMAINS", {"code", "finance"}):
        out = await graph_tools.pkb_graph_communities(domain=domain, min_size=2)
    return out, session


@pytest.mark.asyncio
async def test_no_statement_calls_the_deprecated_id_function():
    _, session = await _run()
    for cypher, _ in session.calls:
        assert not ID_CALL.search(cypher), cypher


@pytest.mark.asyncio
async def test_projection_is_the_aggregation_form_over_nodes():
    _, session = await _run()
    projection = next(c for c, _ in session.calls if "gds.graph.project(" in c)
    assert "gds.graph.project.cypher" not in projection
    assert "gds.graph.project($name, a, b" in projection
    assert "OPTIONAL MATCH (a)-[r]->(b:Artifact)" in projection, "isolated artifacts must still project"
    assert "undirectedRelationshipTypes: ['*']" in projection


@pytest.mark.asyncio
async def test_domain_scopes_both_ends_of_the_projection():
    _, session = await _run(domain="code")
    projection, params = next((c, p) for c, p in session.calls if "gds.graph.project(" in c)
    assert params["domain"] == "code"
    assert "WHERE a.domain = $domain AND coalesce(a.archived, false) = false" in projection
    assert "WHERE b.domain = $domain AND coalesce(b.archived, false) = false" in projection


@pytest.mark.asyncio
async def test_members_leave_the_server_as_application_ids():
    out, session = await _run()
    louvain = next(c for c, _ in session.calls if "gds.louvain.stream" in c)
    assert "collect(gds.util.asNode(nodeId).id) AS member_ids" in louvain
    enrich, params = next((c, p) for c, p in session.calls if "a.id IN $ids" in c)
    assert params["ids"] == ["x1", "x2", "x3"]
    assert out["communities"] == [{
        "community_id": 7,
        "size": 3,
        "artifact_ids": ["x1", "x2", "x3"],
        "top_filenames": ["a.md", "b.md", "c.md"],
        "dominant_domain": "code",
    }]
    assert out["graph_size"] == 3
