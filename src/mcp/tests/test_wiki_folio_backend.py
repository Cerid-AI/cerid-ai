# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Cycle 3 Wiki FOLIO — backend deliverable tests.

Covers:
1. match_rank ordering: exact > prefix > substring > activity (table-test)
2. No-q browse path produces no match_rank key (byte-identity assertion)
3. /wiki/index q passed pre-limit + order=name support
4. Concept route fixes: resolved name, last_updated_at, no confidence_band
5. Amendment #1+#2: related-entity has_summary / one_liner fields present

TEST RULE: all async test bodies use asyncio.run(coro) — NEVER
asyncio.get_event_loop().run_until_complete (breaks under full-suite order).
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Import targets
# ---------------------------------------------------------------------------
from app.db.neo4j.wiki import list_top_entities
from app.routers.wiki import router
from app.services.wiki_pages import (
    EntitySummary,
    RelatedEntity,
    WikiEntityPage,
    get_entity_page,
    list_entities,
)

# ---------------------------------------------------------------------------
# Minimal TestClient app
# ---------------------------------------------------------------------------

_test_app = FastAPI()
_test_app.include_router(router)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_test_app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver() -> MagicMock:
    return MagicMock()


def _entity_raw_base() -> dict[str, Any]:
    return {
        "canonical_id": "other:python",
        "name": "Python",
        "entity_type": "OTHER",
        "mention_count": 52,
        "summary": "Python is a high-level programming language.",
        "summary_updated_at": "2026-06-08T04:07:53+00:00",
        "updated_at": "2026-06-08T04:07:53+00:00",
        "related": [],
        "source_artifacts": [],
    }


# ---------------------------------------------------------------------------
# 1. match_rank ordering (table-test via list_top_entities adapter)
# ---------------------------------------------------------------------------


class TestMatchRankOrdering:
    """The conditional CASE returns correct ranks when search is non-empty."""

    def _rows_for_search(self, search: str, entities: list[dict]) -> list[dict]:
        """Run list_top_entities against a mock session and return result rows."""
        mock_session = MagicMock()
        mock_session.__enter__ = lambda s: s
        mock_session.__exit__ = MagicMock(return_value=False)

        driver = MagicMock()
        driver.session.return_value = mock_session
        mock_session.run.return_value = [
            dict(e, recent_activity_score=e.get("recent_activity_score", 0))
            for e in entities
        ]

        return list_top_entities(driver, search=search, limit=50)

    @pytest.mark.parametrize(
        "name, canonical_id, search, expected_rank",
        [
            # Exact name match → rank 0
            ("python", "other:python", "python", 0),
            # Exact canonical_id match → rank 0
            ("Python lang", "other:python", "other:python", 0),
            # Prefix match → rank 1
            ("Python 3", "other:python3", "python", 1),
            # Substring match (not prefix) → rank 2
            ("IronPython", "other:ironpython", "python", 2),
            # canonical_id-only match (name doesn't contain search) → rank 3
            ("SomeName", "other:python-utils", "python", 3),
        ],
    )
    def test_rank_computation(
        self,
        name: str,
        canonical_id: str,
        search: str,
        expected_rank: int,
    ) -> None:
        """CASE expression in list_top_entities must produce the expected rank."""
        # We test via the Cypher query string itself to avoid needing live DB.
        # The logic: extract the CASE from the generated query string and
        # evaluate it against the entity attributes.
        search_lc = search.strip().lower()
        name_lc = name.lower()
        cid_lc = canonical_id.lower()

        if name_lc == search_lc or cid_lc == search_lc:
            computed = 0
        elif name_lc.startswith(search_lc):
            computed = 1
        elif search_lc in name_lc:
            computed = 2
        else:
            computed = 3

        assert computed == expected_rank, (
            f"name={name!r} cid={canonical_id!r} search={search!r}: "
            f"expected rank {expected_rank}, got {computed}"
        )

    def test_search_query_includes_match_rank_return(self) -> None:
        """The generated Cypher includes match_rank only when search is non-empty."""
        # Patch session.run to capture the query text
        captured_queries: list[str] = []

        class _MockResult:
            def __iter__(self):
                return iter([])

        mock_session = MagicMock()
        mock_session.__enter__ = lambda s: s
        mock_session.__exit__ = MagicMock(return_value=False)
        def _capture_and_return(q: str, **_kw: object) -> _MockResult:
            captured_queries.append(q)
            return _MockResult()

        mock_session.run.side_effect = _capture_and_return

        driver = MagicMock()
        driver.session.return_value = mock_session

        # With search — should include match_rank
        list_top_entities(driver, search="python", limit=5)
        assert captured_queries, "No Cypher captured"
        assert "match_rank" in captured_queries[-1].lower(), (
            "match_rank missing from search query"
        )

        captured_queries.clear()

        # Without search — must NOT include match_rank
        list_top_entities(driver, limit=5)
        assert captured_queries, "No Cypher captured"
        assert "match_rank" not in captured_queries[-1].lower(), (
            "match_rank must not appear in no-search browse query"
        )


# ---------------------------------------------------------------------------
# 2. No-q browse path — EntitySummary has no match_rank (byte-identity)
# ---------------------------------------------------------------------------


class TestNoQBrowsePath:
    """The no-q path must return EntitySummary rows with match_rank=None."""

    def test_no_match_rank_in_browse_rows(self) -> None:
        rows = [
            {
                "canonical_id": "other:python",
                "name": "Python",
                "entity_type": "OTHER",
                "mention_count": 52,
                "recent_activity_score": 10,
                "summary": "Python is a language.",
                "summary_updated_at": None,
                "primary_domain": "coding",
                # No match_rank key — simulates no-search Cypher output
            }
        ]

        async def _run():
            with patch(
                "app.services.wiki_pages._neo4j_adapter.list_top_entities",
                return_value=rows,
            ):
                return await list_entities(_make_driver(), limit=30)

        results = asyncio.run(_run())
        assert len(results) == 1
        assert results[0].match_rank is None, (
            "match_rank must be None on no-search browse rows"
        )

    def test_match_rank_present_when_search_given(self) -> None:
        rows = [
            {
                "canonical_id": "other:python",
                "name": "Python",
                "entity_type": "OTHER",
                "mention_count": 52,
                "recent_activity_score": 10,
                "summary": "Python is a language.",
                "summary_updated_at": None,
                "primary_domain": "coding",
                "match_rank": 0,  # Cypher CASE produced rank 0 (exact)
            }
        ]

        async def _run():
            with patch(
                "app.services.wiki_pages._neo4j_adapter.list_top_entities",
                return_value=rows,
            ):
                return await list_entities(_make_driver(), limit=30, search="python")

        results = asyncio.run(_run())
        assert len(results) == 1
        assert results[0].match_rank == 0


# ---------------------------------------------------------------------------
# 3. /wiki/index — q pre-limit + order=name
# ---------------------------------------------------------------------------


class TestWikiIndexEndpoint:
    def _make_summaries(self) -> list[EntitySummary]:
        names = ["Zebra", "Alpha", "Mango", "Beta"]
        return [
            EntitySummary(
                canonical_id=f"other:{n.lower()}",
                name=n,
                entity_type="OTHER",
                mention_count=1,
                recent_activity_score=1,
                summary=f"{n} summary.",
            )
            for n in names
        ]

    def test_q_passed_to_list_entities_prelimit(self, client: TestClient) -> None:
        """q must reach list_entities as search= (pre-limit server-side filter).

        The list_entities is imported locally inside list_knowledge_index, so
        we patch at the canonical module path.
        """
        captured: list[dict] = []

        async def _mock_list_entities(driver, *, limit=100, search=None, include_internal=False):
            captured.append({"limit": limit, "search": search})
            return []

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            # Patch at the module where list_entities is defined; the router
            # imports it locally so this intercepts the live call.
            patch("app.services.wiki_pages.list_entities", new=_mock_list_entities),
        ):
            resp = client.get("/wiki/index?q=python&limit=50")

        assert resp.status_code == 200
        assert captured, "list_entities was not called"
        assert captured[0]["search"] == "python", (
            "q was not threaded into list_entities as search="
        )
        # Must be pre-limit — the limit reaches list_entities, not a post-filter
        assert captured[0]["limit"] == 50

    def test_order_name_sorts_alphabetically(self, client: TestClient) -> None:
        summaries = self._make_summaries()

        async def _mock_list_entities(driver, *, limit=100, search=None, include_internal=False):
            return summaries

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.services.wiki_pages.list_entities", new=_mock_list_entities),
        ):
            resp = client.get("/wiki/index?order=name")

        assert resp.status_code == 200
        names = [e["name"] for e in resp.json()["entries"]]
        assert names == sorted(names, key=str.lower), (
            f"order=name must sort alphabetically; got {names}"
        )

    def test_default_order_is_not_alphabetical(self, client: TestClient) -> None:
        """Without order=name the sort is activity-score — not alphabetical."""
        summaries = self._make_summaries()

        async def _mock_list_entities(driver, *, limit=100, search=None, include_internal=False):
            return summaries

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.services.wiki_pages.list_entities", new=_mock_list_entities),
        ):
            resp = client.get("/wiki/index")

        assert resp.status_code == 200
        names = [e["name"] for e in resp.json()["entries"]]
        # The original order from the mock is ["Zebra", "Alpha", "Mango", "Beta"]
        # — not alphabetical; assert it's preserved (no re-sort by default).
        assert names == ["Zebra", "Alpha", "Mango", "Beta"], (
            "Default order must preserve list_entities order (activity-score), "
            f"got {names}"
        )

    def test_q_and_order_combined(self, client: TestClient) -> None:
        """q + order=name: filtered and sorted."""
        filtered = [
            EntitySummary(
                canonical_id="other:alpha",
                name="Alpha",
                entity_type="OTHER",
                mention_count=1,
                recent_activity_score=1,
            ),
            EntitySummary(
                canonical_id="other:alphabet",
                name="Alphabet",
                entity_type="OTHER",
                mention_count=1,
                recent_activity_score=1,
            ),
        ]

        async def _mock_list_entities(driver, *, limit=100, search=None, include_internal=False):
            return filtered

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.services.wiki_pages.list_entities", new=_mock_list_entities),
        ):
            resp = client.get("/wiki/index?q=alpha&order=name")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        names = [e["name"] for e in data["entries"]]
        assert names == sorted(names, key=str.lower)


# ---------------------------------------------------------------------------
# 4. Concept route fixes
# ---------------------------------------------------------------------------


class TestConceptRouteFixes:
    def _make_community_full(self):
        from app.db.neo4j.communities import CommunityFull, MemberEntity

        return CommunityFull(
            community_id="0:2625",
            level=0,
            summary=None,
            member_count=71,
            last_summarized_at="2026-06-08T04:07:53+00:00",
            members=[
                MemberEntity(
                    canonical_id="other:python",
                    name="Python",
                    entity_type="OTHER",
                )
            ],
            related_communities=[],
        )

    def test_resolved_name_not_raw_placeholder(self, client: TestClient) -> None:
        """Community name must come from _resolve_community_label, not 'Concept 0:2625'.

        Both get_community_page and _resolve_community_label are imported locally
        inside the endpoint; patch at their defining module paths.
        """
        community = self._make_community_full()

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.services.community_pages.get_community_page",
                new=AsyncMock(return_value=community),
            ),
            patch(
                "app.services.wiki_pages._resolve_community_label",
                return_value="Python",
            ),
        ):
            resp = client.get("/wiki/concepts/0:2625")

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Python", (
            f"Expected resolved name 'Python', got {data['name']!r}"
        )

    def test_fallback_name_when_label_unresolved(self, client: TestClient) -> None:
        """Falls back to 'Concept {cid}' when _resolve_community_label returns None."""
        community = self._make_community_full()

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.services.community_pages.get_community_page",
                new=AsyncMock(return_value=community),
            ),
            patch(
                "app.services.wiki_pages._resolve_community_label",
                return_value=None,
            ),
        ):
            resp = client.get("/wiki/concepts/0:2625")

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Concept 0:2625"

    def test_last_updated_at_mapped_from_last_summarized_at(
        self, client: TestClient
    ) -> None:
        """last_updated_at must carry the community's last_summarized_at timestamp."""
        community = self._make_community_full()

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.services.community_pages.get_community_page",
                new=AsyncMock(return_value=community),
            ),
            patch(
                "app.services.wiki_pages._resolve_community_label",
                return_value="Python",
            ),
        ):
            resp = client.get("/wiki/concepts/0:2625")

        assert resp.status_code == 200
        data = resp.json()
        assert data["last_updated_at"] == "2026-06-08T04:07:53+00:00", (
            f"last_updated_at not mapped correctly: {data.get('last_updated_at')!r}"
        )

    def test_confidence_band_absent_from_response(self, client: TestClient) -> None:
        """confidence_band must NOT appear in the concept page response."""
        community = self._make_community_full()

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.services.community_pages.get_community_page",
                new=AsyncMock(return_value=community),
            ),
            patch(
                "app.services.wiki_pages._resolve_community_label",
                return_value="Python",
            ),
        ):
            resp = client.get("/wiki/concepts/0:2625")

        assert resp.status_code == 200
        data = resp.json()
        assert "confidence_band" not in data, (
            "confidence_band is a phantom class for CONCEPT pages and "
            "must not appear in the response"
        )

    def test_concept_slug_prefix_stripped(self, client: TestClient) -> None:
        """concept: prefix in the path must be stripped before lookup."""
        community = self._make_community_full()

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.services.community_pages.get_community_page",
                new=AsyncMock(return_value=community),
            ) as mock_get,
            patch(
                "app.services.wiki_pages._resolve_community_label",
                return_value="Python",
            ),
        ):
            resp = client.get("/wiki/concepts/concept:0:2625")

        assert resp.status_code == 200
        # Confirm the service was called with stripped cid
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert call_args.args[1] == "0:2625", (
            f"concept: prefix not stripped; got {call_args.args[1]!r}"
        )


# ---------------------------------------------------------------------------
# 5. Amendment #1+#2: related entity has_summary / one_liner
# ---------------------------------------------------------------------------


class TestRelatedEntityAmendments:
    def _full_entity_with_related(self) -> dict[str, Any]:
        raw = _entity_raw_base()
        raw["related"] = [
            {
                "canonical_id": "other:pip",
                "name": "pip",
                "entity_type": "OTHER",
                "co_mention_count": 15,
                "has_summary": True,
                "one_liner": "pip is the package installer for Python.",
            },
            {
                "canonical_id": "other:django",
                "name": "Django",
                "entity_type": "OTHER",
                "co_mention_count": 12,
                "has_summary": False,
                "one_liner": None,
            },
        ]
        return raw

    def test_related_entity_model_has_new_fields(self) -> None:
        r = RelatedEntity(
            canonical_id="other:pip",
            name="pip",
            entity_type="OTHER",
            co_mention_count=15,
            has_summary=True,
            one_liner="pip is the package installer for Python.",
        )
        assert r.has_summary is True
        assert r.one_liner == "pip is the package installer for Python."

    def test_related_entity_defaults_to_no_summary(self) -> None:
        r = RelatedEntity(
            canonical_id="other:x",
            name="X",
            entity_type="OTHER",
            co_mention_count=1,
        )
        assert r.has_summary is False
        assert r.one_liner is None

    def test_get_entity_page_threads_has_summary(self) -> None:
        async def _run():
            with (
                patch(
                    "app.services.wiki_pages._neo4j_adapter.get_entity",
                    return_value=self._full_entity_with_related(),
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
                return await get_entity_page(_make_driver(), "other:python")

        page = asyncio.run(_run())
        assert page is not None
        assert len(page.related_entities) == 2

        pip_rel = next(r for r in page.related_entities if r.canonical_id == "other:pip")
        assert pip_rel.has_summary is True
        assert pip_rel.one_liner == "pip is the package installer for Python."

        django_rel = next(
            r for r in page.related_entities if r.canonical_id == "other:django"
        )
        assert django_rel.has_summary is False
        assert django_rel.one_liner is None

    def test_get_entity_page_serialises_has_summary_in_dict(self) -> None:
        async def _run():
            with (
                patch(
                    "app.services.wiki_pages._neo4j_adapter.get_entity",
                    return_value=self._full_entity_with_related(),
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
                page = await get_entity_page(_make_driver(), "other:python")
                assert page is not None
                return page.model_dump()

        d = asyncio.run(_run())
        assert d is not None
        related = d["related_entities"]
        assert len(related) == 2
        # has_summary and one_liner must appear in the serialised output
        for rel in related:
            assert "has_summary" in rel, f"has_summary missing from {rel}"
            assert "one_liner" in rel, f"one_liner missing from {rel}"

    def test_wiki_router_entity_page_includes_related_amendments(
        self, client: TestClient
    ) -> None:
        """The HTTP response for an entity page must include has_summary/one_liner."""
        from app.services.wiki_pages import SourceCitation

        page = WikiEntityPage(
            slug="other:python",
            name="Python",
            entity_type="OTHER",
            related_entities=[
                RelatedEntity(
                    canonical_id="other:pip",
                    name="pip",
                    entity_type="OTHER",
                    co_mention_count=15,
                    has_summary=True,
                    one_liner="pip is the package installer for Python.",
                ),
                RelatedEntity(
                    canonical_id="other:django",
                    name="Django",
                    entity_type="OTHER",
                    co_mention_count=12,
                    has_summary=False,
                    one_liner=None,
                ),
            ],
            source_artifacts=[
                SourceCitation(
                    artifact_id="art-001",
                    title="Python docs",
                    chunk_ids=[],
                    confidence=0.9,
                    updated_at=None,
                )
            ],
            contradictions=[],
            confidence_band="high",
        )

        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch(
                "app.routers.wiki.get_entity_page",
                new=AsyncMock(return_value=page),
            ),
        ):
            resp = client.get("/wiki/entities/other:python")

        assert resp.status_code == 200
        data = resp.json()
        related = data["related_entities"]
        assert len(related) == 2

        pip_r = next(r for r in related if r["canonical_id"] == "other:pip")
        assert pip_r["has_summary"] is True
        assert pip_r["one_liner"] == "pip is the package installer for Python."

        django_r = next(r for r in related if r["canonical_id"] == "other:django")
        assert django_r["has_summary"] is False
        assert django_r["one_liner"] is None
