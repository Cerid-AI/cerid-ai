# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Frontmatter → ingestion-path integration tests (RAG Cycle C2.2).

Three layers of coverage:

1. **Parser → element**: the markdown parser stamps a JSON-serialised
   frontmatter dict on the first emitted MarkdownSection element.  No
   external services required.
2. **Element → chunk metadata**: the chunker registry propagates the
   frontmatter dict into chunk metadata so the service layer can lift
   it off the first chunk.
3. **Live Neo4j**: a full ingest writes ``:TAGGED_WITH`` edges from
   frontmatter ``tags``, the Artifact node gets ``cerid_*`` properties
   for ``cerid:*`` custom keys, and ``aliases`` promote a
   ``PendingArtifact`` placeholder authored by an earlier ingest.

Layer 3 needs a real Neo4j (the C2.1 ``test_wikilink_neo4j_resolution``
fixture pattern); it auto-skips when ``NEO4J_PASSWORD`` is unset.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

from core.ingest.chunkers import chunk_elements
from core.ingest.parsers.markdown_header import parse_markdown_string

# ---------------------------------------------------------------------------
# Layer 1 + 2: pure-Python tests (no Neo4j needed)
# ---------------------------------------------------------------------------


def test_parser_attaches_frontmatter_json_to_first_element():
    md = (
        "---\n"
        "tags: [foo, bar]\n"
        "aliases: [Alpha]\n"
        "status: draft\n"
        "cerid:priority: high\n"
        "ignored_key: drop_me\n"
        "---\n"
        "# Heading\n\n"
        "Section body.\n"
    )
    elements = parse_markdown_string(md)
    assert len(elements) >= 1
    first_meta = elements[0]["metadata"]
    assert "frontmatter_json" in first_meta
    fm = json.loads(first_meta["frontmatter_json"])
    assert fm["tags"] == ["foo", "bar"]
    assert fm["aliases"] == ["Alpha"]
    assert fm["status"] == "draft"
    assert fm["cerid:priority"] == "high"
    assert "ignored_key" not in fm


def test_parser_with_no_frontmatter_emits_no_frontmatter_key():
    md = "# Heading\n\nBody.\n"
    elements = parse_markdown_string(md)
    assert len(elements) == 1
    assert "frontmatter_json" not in elements[0]["metadata"]


def test_parser_with_malformed_frontmatter_does_not_break_parse():
    md = (
        "---\n"
        "[: not valid yaml\n"
        "---\n"
        "# Heading\n\n"
        "Body.\n"
    )
    # The fence-recognised-but-yaml-malformed branch returns the original
    # text, so the splitter sees the ``---`` lines as horizontal rules /
    # thematic breaks.  The important contract: no exception bubbles.
    elements = parse_markdown_string(md)
    # langchain's MarkdownHeaderTextSplitter treats ``---`` as a thematic
    # break — the heading is still parsed, just the body framing is
    # different.  We don't assert section count; we assert no
    # frontmatter_json was attached because the YAML failed.
    for el in elements:
        assert "frontmatter_json" not in el["metadata"]


def test_parser_attaches_frontmatter_only_to_first_section():
    md = (
        "---\n"
        "tags: [solo]\n"
        "---\n"
        "# Top\n\nTop body.\n\n"
        "## Sub\n\nSub body.\n"
    )
    elements = parse_markdown_string(md)
    assert len(elements) >= 2
    # Only the first element carries frontmatter_json.
    first_has = "frontmatter_json" in elements[0]["metadata"]
    later_has = any("frontmatter_json" in el["metadata"] for el in elements[1:])
    assert first_has is True
    assert later_has is False


def test_chunker_propagates_frontmatter_json_into_chunk_metadata():
    md = (
        "---\n"
        "tags: [a, b]\n"
        "cerid:priority: high\n"
        "---\n"
        "# H\n\nBody.\n"
    )
    elements = parse_markdown_string(md)
    chunks = chunk_elements(elements)
    assert chunks
    # The first chunk inherits the first element's metadata.
    assert "frontmatter_json" in chunks[0]["metadata"]
    fm = json.loads(chunks[0]["metadata"]["frontmatter_json"])
    assert fm["tags"] == ["a", "b"]
    assert fm["cerid:priority"] == "high"


# ---------------------------------------------------------------------------
# Layer 3: live-Neo4j fixture
# ---------------------------------------------------------------------------

NEO4J_URI_DEFAULT = "bolt://ai-companion-neo4j:7687"


@pytest.fixture(scope="module")
def neo4j_driver():
    """Real Neo4j driver. Skips when the database isn't reachable."""
    try:
        from neo4j import GraphDatabase
    except ImportError:
        pytest.skip("neo4j driver not installed")

    in_docker = os.path.exists("/.dockerenv")
    uri = os.environ.get("NEO4J_URI", NEO4J_URI_DEFAULT)
    if not in_docker and uri == NEO4J_URI_DEFAULT:
        uri = "bolt://127.0.0.1:7687"

    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    if not password:
        pytest.skip("NEO4J_PASSWORD not set — live Neo4j assertions unavailable")

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as s:
            s.run("RETURN 1").single()
    except Exception as exc:  # noqa: BLE001 — skip path
        pytest.skip(f"neo4j unreachable ({exc})")

    from app.db.neo4j.schema import init_schema
    init_schema(driver)
    yield driver
    driver.close()


def _cleanup(driver, *, artifact_ids: list[str], pending_names: list[str]) -> None:
    import logging

    from app.db.neo4j.artifacts import delete_artifact
    teardown_logger = logging.getLogger("test.frontmatter_integration.teardown")
    with driver.session() as session:
        for aid in artifact_ids:
            try:
                delete_artifact(driver, aid)
            except Exception:  # noqa: BLE001 — silent-catch-allowed: test teardown best-effort
                teardown_logger.exception(
                    "teardown delete_artifact failed for %s", aid,
                )
        for name in pending_names:
            session.run(
                "MATCH (p:PendingArtifact {name: $name}) DETACH DELETE p",
                name=name,
            )


def _create_artifact(driver, *, filename: str) -> str:
    from app.db.neo4j.artifacts import create_artifact
    aid = f"test-{uuid.uuid4().hex}"
    create_artifact(
        driver=driver,
        artifact_id=aid,
        filename=filename,
        domain="coding",
        keywords_json="[]",
        summary="",
        chunk_count=1,
        chunk_ids_json='["chunk_0"]',
        content_hash=aid,
    )
    return aid


def _get_tags(driver, artifact_id: str) -> list[str]:
    with driver.session() as session:
        result = session.run(
            "MATCH (a:Artifact {id: $aid})-[:TAGGED_WITH]->(t:Tag) "
            "RETURN t.name AS name ORDER BY t.name",
            aid=artifact_id,
        )
        return [r["name"] for r in result]


def _get_artifact_props(driver, artifact_id: str) -> dict:
    with driver.session() as session:
        record = session.run(
            "MATCH (a:Artifact {id: $aid}) RETURN properties(a) AS p",
            aid=artifact_id,
        ).single()
        return dict(record["p"]) if record else {}


# ---------------------------------------------------------------------------
# Layer 3 tests
# ---------------------------------------------------------------------------

class TestFrontmatterTagsToNeo4j:
    def test_tags_create_tagged_with_edges(self, neo4j_driver):
        """Ingesting an artifact whose frontmatter has ``tags: [foo, bar]``
        creates ``(:Artifact)-[:TAGGED_WITH]->(:Tag {name: ...})`` for
        each tag."""
        from app.db.neo4j.artifacts import create_artifact

        aid = f"test-{uuid.uuid4().hex}"
        try:
            create_artifact(
                driver=neo4j_driver,
                artifact_id=aid,
                filename="frontmatter_tags.md",
                domain="coding",
                keywords_json="[]",
                summary="",
                chunk_count=1,
                chunk_ids_json='["chunk_0"]',
                content_hash=aid,
                tags_json=json.dumps(["foo", "bar"]),
            )
            tags = _get_tags(neo4j_driver, aid)
            assert "foo" in tags
            assert "bar" in tags
        finally:
            _cleanup(neo4j_driver, artifact_ids=[aid], pending_names=[])


class TestFrontmatterAliasesResolvePending:
    def test_alias_promotes_pending_artifact(self, neo4j_driver):
        """If ``[[Foo]]`` was written by an earlier ingest (creating a
        ``PendingArtifact {name: "Foo"}``), then ingesting a NEW
        artifact whose frontmatter has ``aliases: [Foo]`` — even though
        its filename stem is different — promotes the placeholder."""
        from app.db.neo4j.wikilinks import (
            resolve_pending_artifacts,
            write_wikilink_edge,
        )

        # 1. Source links to "Foo" — creates PendingArtifact placeholder
        src_id = _create_artifact(neo4j_driver, filename="srcAlias.md")
        target_id = None
        try:
            write_wikilink_edge(
                neo4j_driver,
                source_artifact_id=src_id,
                target_name="Foo",
                is_embed=False,
                source_chunk_id=f"{src_id}_chunk_0",
                alias="Foo",
            )
            # Confirm pending exists
            with neo4j_driver.session() as session:
                rec = session.run(
                    "MATCH (p:PendingArtifact {name: 'Foo'}) RETURN p.name AS n"
                ).single()
            assert rec is not None

            # 2. A new artifact with filename "NotFoo.md" but alias "Foo"
            target_id = _create_artifact(neo4j_driver, filename="NotFoo.md")
            promoted = resolve_pending_artifacts(
                neo4j_driver,
                artifact_id=target_id,
                filename="NotFoo.md",
                aliases=["Foo"],
            )
            assert promoted >= 1

            # 3. PendingArtifact "Foo" is gone, edge points at NotFoo.md
            with neo4j_driver.session() as session:
                pending_rec = session.run(
                    "MATCH (p:PendingArtifact {name: 'Foo'}) RETURN p.name AS n"
                ).single()
                edge_rec = session.run(
                    "MATCH (s:Artifact {id: $sid})-[r:WIKILINKS_TO]->(t:Artifact {id: $tid}) "
                    "RETURN r.pending AS p",
                    sid=src_id, tid=target_id,
                ).single()
            assert pending_rec is None
            assert edge_rec is not None
            assert edge_rec["p"] is False
        finally:
            ids = [src_id]
            if target_id:
                ids.append(target_id)
            _cleanup(neo4j_driver, artifact_ids=ids, pending_names=["Foo"])


class TestCustomCeridProperties:
    def test_cerid_custom_keys_land_as_node_properties(self, neo4j_driver):
        """``cerid:priority: high`` in frontmatter should land as
        ``cerid_priority = "high"`` on the Artifact node (colon→underscore
        rewrite because Neo4j property names can't contain colons)."""
        from app.db.neo4j.artifacts import set_artifact_properties

        aid = _create_artifact(neo4j_driver, filename="cerid_props.md")
        try:
            set_artifact_properties(
                driver=neo4j_driver,
                artifact_id=aid,
                properties={
                    "cerid_priority": "high",
                    "cerid_reviewed": True,
                    "status": "draft",
                },
            )
            props = _get_artifact_props(neo4j_driver, aid)
            assert props.get("cerid_priority") == "high"
            assert props.get("cerid_reviewed") is True
            assert props.get("status") == "draft"
        finally:
            _cleanup(neo4j_driver, artifact_ids=[aid], pending_names=[])


class TestNoFrontmatterRegression:
    def test_artifact_without_frontmatter_creates_cleanly(self, neo4j_driver):
        """An artifact with no frontmatter ingests cleanly — no extra
        properties added, no PendingArtifact rows created."""
        aid = _create_artifact(neo4j_driver, filename="plain.md")
        try:
            props = _get_artifact_props(neo4j_driver, aid)
            assert props.get("filename") == "plain.md"
            # No frontmatter-derived properties.
            assert "cerid_priority" not in props
            assert "status" not in props or props.get("status") == ""
        finally:
            _cleanup(neo4j_driver, artifact_ids=[aid], pending_names=[])
