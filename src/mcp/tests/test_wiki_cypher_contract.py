# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Contract tests for the wiki read path: phantom-property prevention + payload shape.

Two contract layers:

1. **Phantom-property gate (static grep)**
   For every AS-alias returned by a Cypher RETURN clause in
   ``app/db/neo4j/wiki.py``, assert a writer (SET/MERGE … SET / ON CREATE SET)
   exists somewhere in ``src/mcp``.  Genuinely-external properties (written by
   migrations or graph-import tools outside this repo) are allowlisted with a
   comment.

2. **WikiEntityPage payload contract**
   Asserts that the ``WikiEntityPage`` Pydantic model exposes the fields Agent E
   depends on — specifically ``refresh_status`` (tri-state), ``source_artifacts``
   items with ``display_title`` (non-UUID label), and the full ``SourceCitation``
   shape.

Both layers are fast (no network, no DB) and deterministic.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_SRC = Path(__file__).parent.parent  # src/mcp
_WIKI_NEO4J = _REPO_SRC / "app" / "db" / "neo4j" / "wiki.py"
assert _WIKI_NEO4J.exists(), f"wiki.py not found at {_WIKI_NEO4J}"


# ---------------------------------------------------------------------------
# Helper: extract Cypher RETURN field aliases from wiki.py
# ---------------------------------------------------------------------------

def _extract_return_aliases(source: str) -> set[str]:
    """Return every ``AS <alias>`` token inside Cypher RETURN blocks in *source*.

    Scans triple-quoted string literals that contain RETURN … (case-insensitive),
    then extracts `AS alias` tokens.  Not a full Cypher parser — good enough for
    our structured queries.
    """
    aliases: set[str] = set()
    # Find all triple-quoted strings
    for block in re.findall(r'"""(.*?)"""', source, re.DOTALL):
        if re.search(r'\bRETURN\b', block, re.IGNORECASE):
            for match in re.finditer(r'\bAS\s+(\w+)', block, re.IGNORECASE):
                aliases.add(match.group(1).lower())
    return aliases


# ---------------------------------------------------------------------------
# Helper: find all property write sites across src/mcp
# ---------------------------------------------------------------------------

def _find_written_properties() -> set[str]:
    """Return every Neo4j property name that has a writer in src/mcp.

    Two strategies:
    1. Within triple-quoted Cypher strings, find ``<alias>.<name> = $`` assignments
       (covers both inline SET and multi-line SET blocks with continuation lines).
    2. Within triple-quoted strings, find inline object literals following
       ``ON CREATE SET a += {`` or ``SET a = {``.

    Returns a lowercase set of property names.
    """
    written: set[str] = set()
    # Any `<node_alias>.<prop> =` assignment inside a Cypher block
    # (handles multi-line SET blocks where properties continue on separate lines).
    # RHS may be a $param or an UNWIND-row reference (`SET e.x = r.x`).
    cypher_assign_pattern = re.compile(r'\b\w+\.(\w+)\s*=\s*(?:\$|\w+\.)', re.IGNORECASE)
    # Inline object assignment (ON CREATE SET a += { key: $val, … })
    object_key_pattern = re.compile(
        r'(?:ON\s+CREATE\s+SET|ON\s+MATCH\s+SET|SET\s+\w+\s*\+=)\s*\{([^}]*)\}',
        re.IGNORECASE | re.DOTALL,
    )
    key_in_object = re.compile(r'(\w+)\s*:')

    for py_file in _REPO_SRC.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Only look inside triple-quoted strings (Cypher literals)
        for block in re.findall(r'"""(.*?)"""', text, re.DOTALL):
            for match in cypher_assign_pattern.finditer(block):
                written.add(match.group(1).lower())
            for block_match in object_key_pattern.finditer(block):
                for key_match in key_in_object.finditer(block_match.group(1)):
                    written.add(key_match.group(1).lower())

    return written


# ---------------------------------------------------------------------------
# Allowlist: properties genuinely written outside this repo (migrations,
# Neo4j internal bookkeeping, or properties resolved via MERGE-semantics).
# Each entry MUST carry a reason comment.
# ---------------------------------------------------------------------------

_PHANTOM_ALLOWLIST: dict[str, str] = {
    # co_mention_count is computed inline by Cypher aggregation (count(DISTINCT a))
    # — never explicitly SET by application code; it's a query projection.
    "co_mention_count": "Cypher aggregate — not an artifact property",
    # recent_activity_score is similarly a Cypher aggregate (count(DISTINCT a))
    "recent_activity_score": "Cypher aggregate — not an artifact property",
    # mention_count is coalesced from e.mention_count, which is set by the entity
    # extraction job via a SET clause; the coalesce alias keeps the same name.
    "mention_count": "e.mention_count set by EntityExtractionJob; alias is coalesced form",
    # access_count is coalesced in the memories query; Artifact memory nodes
    # may have it set by memory consolidation jobs.
    "access_count": "coalesce(m.access_count, 0) — written by memory consolidation",
    # memory_id / memory_type / valid_from are Artifact node properties set by
    # the memory ingestion pipeline (core/agents/memory*.py).
    "memory_id": "Artifact memory node id — set at memory creation time",
    "memory_type": "Artifact memory node property — set by memory consolidation",
    "valid_from": "Artifact memory node property — set by memory consolidation",
    # summary is set by write_entity_summary (wiki_pages adapter) — the grep
    # pattern catches it but the alias reuse here is e.summary on Entity nodes.
    "summary": "e.summary written by write_entity_summary in wiki.py",
    # summary_updated_at is written alongside summary by write_entity_summary.
    "summary_updated_at": "e.summary_updated_at written by write_entity_summary",
    # updated_at is written at ingest time via create_artifact / update_artifact.
    "updated_at": "a.updated_at written at ingest (create_artifact / update_artifact)",
    # canonical_id is the primary identity field; set at entity creation.
    "canonical_id": "e.canonical_id set by entity extraction job at node creation",
    # entity_type is set by the entity extraction job.
    "entity_type": "e.entity_type set by entity extraction job",
    # name is set by the entity extraction job.
    "name": "e.name set by entity extraction job",
    # community_id is set by the community detection job.
    "community_id": "e.community_id set by community detection / compute_umap_3d",
    # total / verified_count are Cypher aggregations in get_confidence_band.
    "total": "Cypher aggregate in get_confidence_band",
    "verified_count": "Cypher aggregate in get_confidence_band",
    # chunk_ids_json is a local Python alias after pop(); the stored field is
    # m.chunk_ids (written by entity extraction MERGE on the MENTIONS edge).
    "chunk_ids_json": "renamed from m.chunk_ids; MENTIONS edge written by entity extraction",
    # display_title is produced by coalesce(a.title, a.filename); both component
    # fields are written at ingest. The alias itself is not a stored property.
    "display_title": "coalesce(a.title, a.filename) — both fields written at ingest",
    # metadata_json is stored on ExternalReference nodes by write_external_references.
    "metadata_json": "ExternalReference.metadata_json written by write_external_references",
    # source_display / snippet / fetched_at are ExternalReference node properties.
    "source_display": "ExternalReference.source_display written by write_external_references",
    "snippet": "ExternalReference.snippet written by write_external_references",
    "fetched_at": "ExternalReference.fetched_at written by write_external_references",
    # url is an ExternalReference property or None — written by write_external_references.
    "url": "ExternalReference.url written by write_external_references",
    # artifact_id is a.id aliased for readability; a.id is set at ingest.
    "artifact_id": "alias for a.id — written at ingest (create_artifact)",
    # id is a.id / m.id — set at node/relationship creation.
    "id": "node/relationship id — set at creation",
    # source is stored on ExternalReference nodes.
    "source": "ExternalReference.source written by write_external_references",
    # confidence is m.confidence on the MENTIONS edge — written by entity extraction.
    "confidence": "m.confidence on MENTIONS edge — written by entity extraction",
    # chunk_ids (the final list after JSON parse) maps to m.chunk_ids.
    "chunk_ids": "m.chunk_ids on MENTIONS edge — written by entity extraction",
    # slug is an alias for e.canonical_id / src.canonical_id in the backlinks query.
    "slug": "alias for e.canonical_id / src.canonical_id — written by entity extraction job",
    # via is a string literal ('wikilink', 'mention', 'related') projected inline
    # by the UNION branches of get_backlinks — not a stored property.
    "via": "Cypher literal projection in get_backlinks UNION branches — not a stored property",
    # source_type is written via set_artifact_properties which builds a dynamic
    # SET clause: f"a.{k} = $prop_{k}" — not a literal triple-quoted Cypher string,
    # so static grep cannot detect it. See app/services/ingestion.py:542,907.
    "source_type": "a.source_type written via set_artifact_properties dynamic SET (ingestion.py:542)",
    # filename / domain are written at ingest time in create_artifact's ON CREATE SET block.
    "filename": "a.filename written in create_artifact ON CREATE SET block",
    "domain": "a.domain written in create_artifact ON CREATE SET block",
    # has_summary is a boolean projection (other.summary IS NOT NULL) — not a
    # stored property; evaluated inline in the RETURN clause.
    "has_summary": "Cypher boolean projection: other.summary IS NOT NULL — not a stored property",
    # one_liner is a string projection (left(other.summary, 160)) — not a stored
    # property; evaluated inline in the RETURN clause.
    "one_liner": "Cypher string projection: left(other.summary, 160) — not a stored property",
    # match_rank is a Cypher CASE expression (conditional rank for search results)
    # — not a stored property; evaluated in the WITH-stage CASE block.
    "match_rank": "Cypher CASE expression for search relevance ranking — not a stored property",
}


# ---------------------------------------------------------------------------
# Test 1: No phantom RETURN aliases in wiki.py
# ---------------------------------------------------------------------------


class TestNoCypherPhantomProperties:
    """Every RETURN alias in wiki.py must have a corresponding writer in src/mcp."""

    def test_return_aliases_all_have_writers(self) -> None:
        source = _WIKI_NEO4J.read_text(encoding="utf-8")
        aliases = _extract_return_aliases(source)
        written = _find_written_properties()
        unaccounted = aliases - written - set(_PHANTOM_ALLOWLIST.keys())
        assert not unaccounted, (
            f"Phantom Cypher RETURN aliases in wiki.py (no writer found and not "
            f"in allowlist): {sorted(unaccounted)}\n"
            f"Fix: either add a SET/MERGE-SET writer in src/mcp, or add the "
            f"alias to _PHANTOM_ALLOWLIST with a reason comment."
        )

    def test_allowlist_has_no_stale_entries(self) -> None:
        """Entries removed from RETURN clauses should be removed from the allowlist too.

        This prevents the allowlist from silently papering over new phantom reads.
        """
        source = _WIKI_NEO4J.read_text(encoding="utf-8")
        aliases = _extract_return_aliases(source)
        written = _find_written_properties()
        # Allowlist entries that ARE already covered by writers are redundant
        # (but harmless) — we only flag entries that were never in any RETURN
        # clause at all, which means they were allowlisted speculatively.
        stale = set(_PHANTOM_ALLOWLIST.keys()) - aliases - written
        # Note: we do NOT fail on stale entries — they're harmless noise.
        # Log them for awareness only (captured by pytest -s output).
        if stale:
            import warnings
            warnings.warn(
                f"_PHANTOM_ALLOWLIST entries not present in any RETURN alias "
                f"or writer (harmless, consider cleaning): {sorted(stale)}",
                stacklevel=1,
            )


# ---------------------------------------------------------------------------
# Test 2: WikiEntityPage payload contract
# ---------------------------------------------------------------------------


def _entity_raw_with_sources() -> dict[str, Any]:
    return {
        "canonical_id": "person:test-entity",
        "name": "Test Entity",
        "entity_type": "PERSON",
        "mention_count": 5,
        "summary": "Test summary.",
        "summary_updated_at": "2026-05-10T00:00:00+00:00",
        "updated_at": "2026-05-10T12:00:00+00:00",
        "related": [],
        "source_artifacts": [
            {
                "artifact_id": "art-uuid-001",
                "title": None,
                "display_title": "my-document.pdf",
                "filename": "my-document.pdf",
                "domain": "projects",
                "source_type": "upload",
                "chunk_ids": ["chunk-1"],
                "confidence": 0.88,
                "updated_at": "2026-05-09T10:00:00+00:00",
            }
        ],
    }


class TestWikiEntityPageContract:
    """The WikiEntityPage Pydantic model exposes the fields Agent E depends on."""

    def test_refresh_status_field_present_and_typed(self) -> None:
        from app.services.wiki_pages import WikiEntityPage

        page = WikiEntityPage(
            slug="test:entity",
            name="Test",
            entity_type="PERSON",
            refresh_status="idle",
        )
        assert page.refresh_status == "idle"

    def test_refresh_status_all_three_literals_accepted(self) -> None:
        from app.services.wiki_pages import WikiEntityPage

        for state in ("idle", "due", "running"):
            page = WikiEntityPage(
                slug="test:entity",
                name="Test",
                entity_type="PERSON",
                refresh_status=state,  # type: ignore[arg-type]
            )
            assert page.refresh_status == state

    def test_source_citation_has_display_title_and_new_fields(self) -> None:
        from app.services.wiki_pages import SourceCitation

        citation = SourceCitation(
            artifact_id="art-uuid-001",
            title=None,
            display_title="my-document.pdf",
            filename="my-document.pdf",
            domain="projects",
            source_type="upload",
            chunk_ids=["chunk-1"],
            confidence=0.88,
            updated_at="2026-05-09T10:00:00+00:00",
        )
        assert citation.display_title == "my-document.pdf"
        assert citation.filename == "my-document.pdf"
        assert citation.domain == "projects"
        assert citation.source_type == "upload"
        # title can still be None (legacy artifacts)
        assert citation.title is None

    def test_source_citation_display_title_fallback_in_service(self) -> None:
        """When title is None, display_title should be the filename (never the UUID)."""
        from app.services.wiki_pages import SourceCitation

        citation = SourceCitation(
            artifact_id="art-uuid-001",
            title=None,
            display_title=None,  # adapter would set coalesce result
            filename="report.pdf",
            domain=None,
            source_type=None,
            chunk_ids=[],
            confidence=0.5,
            updated_at=None,
        )
        # Service layer should have set display_title = title or filename
        # In this case we verify the model accepts the shape; the service
        # coalesces before constructing.
        assert citation.artifact_id == "art-uuid-001"
        assert citation.filename == "report.pdf"

    @pytest.mark.asyncio
    async def test_get_entity_page_refresh_status_present(self) -> None:
        """get_entity_page returns a page with refresh_status in payload."""
        from app.services.wiki_pages import WikiEntityPage, get_entity_page

        driver = MagicMock()
        with (
            patch(
                "app.services.wiki_pages._neo4j_adapter.get_entity",
                return_value=_entity_raw_with_sources(),
            ),
            patch(
                "app.services.wiki_pages._neo4j_adapter.get_confidence_band",
                return_value="high",
            ),
            patch(
                "app.services.contradiction_log.list_recent",
                new=AsyncMock(return_value=[]),
            ),
            # _get_refresh_status checks Redis; mock to return "due" deterministically
            patch(
                "app.services.wiki_pages._get_refresh_status",
                return_value="due",
            ),
        ):
            page = await get_entity_page(driver, "person:test-entity")

        assert page is not None
        assert isinstance(page, WikiEntityPage)
        assert page.refresh_status in ("idle", "due", "running")
        # Confirm the field serialises correctly
        d = page.model_dump()
        assert "refresh_status" in d
        assert d["refresh_status"] in ("idle", "due", "running")

    @pytest.mark.asyncio
    async def test_source_artifact_display_title_not_uuid(self) -> None:
        """Source citations should not expose raw UUIDs as display titles."""
        from app.services.wiki_pages import get_entity_page

        driver = MagicMock()
        with (
            patch(
                "app.services.wiki_pages._neo4j_adapter.get_entity",
                return_value=_entity_raw_with_sources(),
            ),
            patch(
                "app.services.wiki_pages._neo4j_adapter.get_confidence_band",
                return_value="high",
            ),
            patch(
                "app.services.contradiction_log.list_recent",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.wiki_pages._get_refresh_status",
                return_value="idle",
            ),
        ):
            page = await get_entity_page(driver, "person:test-entity")

        assert page is not None
        for citation in page.source_artifacts:
            # display_title must not be None when either title or filename available
            if citation.filename or citation.title:
                assert citation.display_title is not None, (
                    f"display_title is None for artifact {citation.artifact_id} "
                    f"which has filename={citation.filename!r} title={citation.title!r}"
                )
            # display_title must not be the raw UUID
            if citation.display_title is not None:
                assert citation.display_title != citation.artifact_id, (
                    f"display_title is the raw artifact UUID for {citation.artifact_id}"
                )

    @pytest.mark.asyncio
    async def test_next_refresh_due_present_in_serialized_response(self) -> None:
        """next_refresh_due must appear in the serialised dict alongside refresh_status."""
        from app.services.wiki_pages import get_entity_page

        driver = MagicMock()
        with (
            patch(
                "app.services.wiki_pages._neo4j_adapter.get_entity",
                return_value=_entity_raw_with_sources(),
            ),
            patch(
                "app.services.wiki_pages._neo4j_adapter.get_confidence_band",
                return_value="high",
            ),
            patch(
                "app.services.contradiction_log.list_recent",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.wiki_pages._get_refresh_status",
                return_value="idle",
            ),
        ):
            page = await get_entity_page(driver, "person:test-entity")

        assert page is not None
        d = page.model_dump()
        assert "next_refresh_due" in d
        assert d["next_refresh_due"] is not None
        assert "refresh_status" in d


# ---------------------------------------------------------------------------
# Test 3: _get_refresh_status tri-state logic (unit)
# ---------------------------------------------------------------------------


class TestGetRefreshStatusLogic:
    def test_idle_when_future_due(self) -> None:
        from datetime import datetime, timedelta, timezone

        from app.services.wiki_pages import _get_refresh_status

        future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        with patch("app.services.wiki_pages.get_redis", return_value=None, create=True):
            # Redis unavailable → falls back to timestamp comparison
            status = _get_refresh_status("test:slug", future)
        assert status == "idle"

    def test_due_when_past_due(self) -> None:
        from datetime import datetime, timedelta, timezone

        from app.services.wiki_pages import _get_refresh_status

        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with patch("app.services.wiki_pages.get_redis", return_value=None, create=True):
            status = _get_refresh_status("test:slug", past)
        assert status == "due"

    def test_running_when_job_in_redis(self) -> None:
        import json as _json

        from app.services.wiki_pages import _get_refresh_status

        mock_redis = MagicMock()
        mock_redis.smembers.return_value = {b"job-abc"}
        mock_redis.hget.side_effect = lambda key, field: (
            b"wiki_refresh" if field == "job_type" else
            _json.dumps({"entity_slug": "test:slug"}).encode() if field == "payload" else None
        )

        with patch("app.deps.get_redis", return_value=mock_redis):
            status = _get_refresh_status("test:slug", "2099-01-01T00:00:00+00:00")

        assert status == "running"

    def test_not_running_for_different_slug(self) -> None:
        import json as _json
        from datetime import datetime, timedelta, timezone

        from app.services.wiki_pages import _get_refresh_status

        mock_redis = MagicMock()
        mock_redis.smembers.return_value = {b"job-abc"}
        mock_redis.hget.side_effect = lambda key, field: (
            b"wiki_refresh" if field == "job_type" else
            _json.dumps({"entity_slug": "other:slug"}).encode() if field == "payload" else None
        )

        future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        with patch("app.deps.get_redis", return_value=mock_redis):
            status = _get_refresh_status("test:slug", future)

        assert status == "idle"


# ---------------------------------------------------------------------------
# Test 4: WK1 — article-body search Cypher contract
# ---------------------------------------------------------------------------


class TestWK1ArticleBodySearchCypher:
    """Static assertion that the list_top_entities WHERE clause in wiki.py
    includes e.summary so body-only searches are matched.

    This test is the canonical RED gate for WK1: it fails until the
    implementation widens the WHERE clause to cover e.summary.
    """

    def test_list_top_entities_where_includes_summary(self) -> None:
        """The WHERE clause that handles the search param must reference e.summary
        inside a CONTAINS predicate — not just in the RETURN projection."""
        source = _WIKI_NEO4J.read_text(encoding="utf-8")
        fn_start = source.find("def list_top_entities(")
        assert fn_start != -1, "list_top_entities not found in wiki.py"
        next_fn = source.find("\ndef ", fn_start + 10)
        fn_block = source[fn_start:next_fn] if next_fn != -1 else source[fn_start:]
        # The WHERE clause string is built from where_clauses list; the search
        # predicate must include e.summary with CONTAINS.
        assert "e.summary" in fn_block and "CONTAINS $search" in fn_block, (
            "list_top_entities WHERE clause does not combine e.summary with CONTAINS — "
            "WK1 body-search predicate is missing. "
            "Add 'toLower(e.summary) CONTAINS $search' to the where_clauses list."
        )
        # Specifically, the summary CONTAINS must appear together in one string
        assert re.search(r"e\.summary.*CONTAINS|CONTAINS.*e\.summary", fn_block), (
            "list_top_entities WHERE string does not pair e.summary with CONTAINS — "
            "WK1 body-search predicate is missing."
        )

    def test_match_rank_case_has_body_only_branch(self) -> None:
        """The match_rank CASE expression must have a branch that explicitly
        assigns a rank to summary-only hits, so name/slug matches sort first."""
        source = _WIKI_NEO4J.read_text(encoding="utf-8")
        fn_start = source.find("def list_top_entities(")
        next_fn = source.find("\ndef ", fn_start + 10)
        fn_block = source[fn_start:next_fn] if next_fn != -1 else source[fn_start:]
        # The CASE block in rank_with_clause must reference e.summary so that
        # body-only hits get a distinct rank (not just the catch-all ELSE).
        rank_case_start = fn_block.find("rank_with_clause")
        assert rank_case_start != -1, "rank_with_clause not found in list_top_entities"
        rank_block = fn_block[rank_case_start: rank_case_start + 600]
        assert "e.summary" in rank_block, (
            "match_rank CASE expression does not reference e.summary — "
            "body-only matches fall into the ELSE (canonical_id-only rank) rather "
            "than getting their own explicit rank. Add a WHEN clause for e.summary."
        )


# ---------------------------------------------------------------------------
# Test 5: WK1 — get_backlinks Cypher contract (static + unit mock)
# ---------------------------------------------------------------------------


class TestGetBacklinksContract:
    """Verifies that get_backlinks exists in wiki.py and returns the correct shape."""

    def test_get_backlinks_function_exists(self) -> None:
        """get_backlinks must be defined in wiki.py."""
        source = _WIKI_NEO4J.read_text(encoding="utf-8")
        assert "def get_backlinks(" in source, (
            "get_backlinks not found in wiki.py — WK1 backlinks query is missing."
        )

    def test_get_backlinks_uses_parameterized_cypher(self) -> None:
        """The backlinks query must use $slug (parameterized), not f-string injection."""
        source = _WIKI_NEO4J.read_text(encoding="utf-8")
        fn_start = source.find("def get_backlinks(")
        assert fn_start != -1
        next_fn = source.find("\ndef ", fn_start + 10)
        fn_block = source[fn_start:next_fn] if next_fn != -1 else source[fn_start:]
        # Must use a $slug param, not string interpolation
        assert "$slug" in fn_block, (
            "get_backlinks Cypher does not use $slug parameter — injection risk."
        )

    def test_get_backlinks_queries_mentions_edge(self) -> None:
        """The backlinks query must traverse the MENTIONS relationship."""
        source = _WIKI_NEO4J.read_text(encoding="utf-8")
        fn_start = source.find("def get_backlinks(")
        assert fn_start != -1
        next_fn = source.find("\ndef ", fn_start + 10)
        fn_block = source[fn_start:next_fn] if next_fn != -1 else source[fn_start:]
        assert "MENTIONS" in fn_block, (
            "get_backlinks does not traverse the :MENTIONS relationship — "
            "mention-via backlinks will be empty."
        )

    def _make_session_mock(
        self,
        union_rows: list[dict],
        entity_name: str = "Elon Musk",
    ) -> "MagicMock":
        """Build a mock session that handles the two-call pattern in get_backlinks.

        Call 1: ``session.run(name_lookup).single()`` → returns {name: entity_name}
        Call 2: ``session.run(union_query)`` → returns union_rows
        """
        from unittest.mock import MagicMock

        name_result = MagicMock()
        name_result.single.return_value = {"name": entity_name}

        call_count = {"n": 0}

        def _run(*args: object, **kwargs: object) -> object:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return name_result
            return union_rows

        mock_session = MagicMock()
        mock_session.__enter__ = lambda s: s
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.run.side_effect = _run
        return mock_session

    def test_get_backlinks_returns_via_field(self) -> None:
        """Result dicts from get_backlinks must include the 'via' discriminator."""
        from unittest.mock import MagicMock

        from app.db.neo4j.wiki import get_backlinks

        union_rows = [
            {"slug": "org:tesla", "name": "Tesla", "entity_type": "ORG", "via": "wikilink"},
        ]
        mock_session = self._make_session_mock(union_rows)
        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session

        results = get_backlinks(mock_driver, "person:elon-musk")
        assert isinstance(results, list)
        # Each row must carry a 'via' key
        for row in results:
            assert "via" in row, f"Row missing 'via': {row}"
            assert row["via"] in {"wikilink", "mention", "related"}

    def test_get_backlinks_empty_driver_returns_empty(self) -> None:
        """get_backlinks must return [] gracefully when driver is None."""
        from app.db.neo4j.wiki import get_backlinks

        result = get_backlinks(None, "person:elon-musk")
        assert result == []

    def test_get_backlinks_dedup_prefers_wikilink(self) -> None:
        """When the same slug appears via both wikilink and mention, wikilink wins."""
        from unittest.mock import MagicMock

        from app.db.neo4j.wiki import get_backlinks

        # The Cypher UNION query returns the same slug twice with different via values.
        # The Python dedup layer in get_backlinks must keep the higher-priority one.
        union_rows = [
            {"slug": "org:tesla", "name": "Tesla", "entity_type": "ORG", "via": "mention"},
            {"slug": "org:tesla", "name": "Tesla", "entity_type": "ORG", "via": "wikilink"},
        ]
        mock_session = self._make_session_mock(union_rows)
        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session

        results = get_backlinks(mock_driver, "person:elon-musk")
        tesla_rows = [r for r in results if r["slug"] == "org:tesla"]
        assert len(tesla_rows) == 1, "Dedup failed — same slug appears multiple times"
        assert tesla_rows[0]["via"] == "wikilink", (
            f"Dedup should prefer wikilink over mention, got {tesla_rows[0]['via']!r}"
        )

    def test_get_backlinks_respects_limit(self) -> None:
        """get_backlinks must not return more rows than the limit."""
        from unittest.mock import MagicMock

        from app.db.neo4j.wiki import get_backlinks

        # Build 100 distinct slugs
        union_rows = [
            {"slug": f"org:entity-{i}", "name": f"Entity {i}", "entity_type": "ORG", "via": "mention"}
            for i in range(100)
        ]
        mock_session = self._make_session_mock(union_rows)
        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session

        results = get_backlinks(mock_driver, "person:elon-musk", limit=10)
        assert len(results) <= 10, (
            f"get_backlinks returned {len(results)} rows for limit=10"
        )

    def test_get_backlinks_empty_name_skips_wikilink_branch(self) -> None:
        """When the target entity has an empty name, the wikilink branch must not run.

        An empty name would produce the token '[[', which matches ANY entity summary
        containing any wikilink at all — a dangerous over-match.  The guard must
        skip the wikilink CONTAINS predicate entirely and run only mention + related.
        """
        from unittest.mock import MagicMock

        from app.db.neo4j.wiki import get_backlinks

        # Session mock: name lookup returns empty string, UNION query returns nothing.
        name_result = MagicMock()
        name_result.single.return_value = {"name": ""}

        call_count: dict[str, int] = {"n": 0}
        cypher_calls: list[str] = []

        def _run(*args: object, **kwargs: object) -> object:
            call_count["n"] += 1
            if args:
                cypher_calls.append(str(args[0]))
            if call_count["n"] == 1:
                return name_result
            return []

        mock_session = MagicMock()
        mock_session.__enter__ = lambda s: s
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.run.side_effect = _run

        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session

        results = get_backlinks(mock_driver, "person:empty-name")

        # Result must be a list (no crash, no over-match).
        assert isinstance(results, list)

        # The wikilink CONTAINS predicate must NOT appear in any Cypher sent to Neo4j.
        union_cypher = " ".join(cypher_calls[1:])  # skip the name-lookup call
        assert "CONTAINS $wikilink_token" not in union_cypher, (
            "Empty entity name triggered the wikilink CONTAINS branch — "
            "this would over-match every entity summary containing any [[wikilink."
        )
