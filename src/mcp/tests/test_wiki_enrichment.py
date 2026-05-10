# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the wiki enrichment orchestrator (Phase API.3).

All adapters are mocked — no real network calls.
All registry.is_enabled calls use a mock registry module.

Coverage:
- infer_entity_type for at least 4 entity types
- enrich() dispatches to correct adapters per entity type
- enrich() skips disabled adapters
- enrich() continues past a failing adapter; failure is logged not raised
- enrich() returns empty list when all adapters are disabled
- _to_external_reference normalises each adapter shape
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.external_apis.wiki_enrichment import (
    ADAPTER_ROUTING,
    enrich,
    infer_entity_type,
)
from app.services.wiki_pages import ExternalReference

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(enabled_slugs: set[str]) -> Any:
    """Return a simple mock registry that enables the given slugs."""
    mod = MagicMock()
    mod.is_enabled.side_effect = lambda slug: slug in enabled_slugs
    return mod


def _all_enabled_registry() -> Any:
    mod = MagicMock()
    mod.is_enabled.return_value = True
    return mod


def _all_disabled_registry() -> Any:
    mod = MagicMock()
    mod.is_enabled.return_value = False
    return mod


# ---------------------------------------------------------------------------
# infer_entity_type
# ---------------------------------------------------------------------------


class TestInferEntityType:
    def test_isbn_13_is_book(self):
        assert infer_entity_type("9780140449136") == "book"

    def test_isbn_10_is_book(self):
        assert infer_entity_type("0140449132") == "book"

    def test_github_repo_is_repository(self):
        assert infer_entity_type("owner/repo-name") == "repository"
        assert infer_entity_type("Cerid-AI/cerid-ai") == "repository"

    def test_npm_scoped_is_package_npm(self):
        assert infer_entity_type("@scope/package-name") == "package_npm"
        assert infer_entity_type("@babel/core") == "package_npm"

    def test_canonical_id_person_prefix(self):
        assert infer_entity_type("person:elon-musk") == "person"

    def test_canonical_id_place_prefix(self):
        assert infer_entity_type("place:berlin") == "place"

    def test_canonical_id_package_pypi_prefix(self):
        assert infer_entity_type("package_pypi:httpx") == "package_pypi"

    def test_canonical_id_repository_prefix(self):
        assert infer_entity_type("repository:my-repo") == "repository"

    def test_canonical_id_concept_prefix(self):
        assert infer_entity_type("concept:machine-learning") == "concept"

    def test_place_keyword_in_name(self):
        # "city" is in the place-word set
        assert infer_entity_type("New York city") == "place"

    def test_paren_disambig_programming_language(self):
        result = infer_entity_type("Python (programming language)")
        assert result == "concept"

    def test_paren_disambig_book(self):
        result = infer_entity_type("Dune (novel)")
        assert result == "book"

    def test_fallthrough_unknown(self):
        assert infer_entity_type("SomeRandomThing") == "unknown"

    def test_accepts_related_entities_param(self):
        # Should not raise with related_entities supplied
        result = infer_entity_type("Tesla", related_entities=["Elon Musk", "SpaceX"])
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# ADAPTER_ROUTING sanity
# ---------------------------------------------------------------------------


class TestAdapterRouting:
    def test_all_entity_types_have_routes(self):
        expected = {"person", "concept", "place", "book", "package_pypi", "package_npm", "repository", "unknown"}
        assert set(ADAPTER_ROUTING.keys()) == expected

    def test_wikipedia_is_person_route(self):
        assert "wikipedia" in ADAPTER_ROUTING["person"]

    def test_packages_is_pypi_route(self):
        assert "packages" in ADAPTER_ROUTING["package_pypi"]

    def test_github_is_repository_route(self):
        assert "github" in ADAPTER_ROUTING["repository"]

    def test_osm_is_place_route(self):
        assert "osm" in ADAPTER_ROUTING["place"]

    def test_openlibrary_is_book_route(self):
        assert "openlibrary" in ADAPTER_ROUTING["book"]

    def test_wikipedia_is_unknown_fallback(self):
        assert "wikipedia" in ADAPTER_ROUTING["unknown"]


# ---------------------------------------------------------------------------
# enrich() — adapter dispatch
# ---------------------------------------------------------------------------


class TestEnrichDispatch:
    async def test_person_dispatches_wikipedia(self):
        """For a person entity, Wikipedia adapter is called."""
        wiki_result = {
            "title": "Elon Musk",
            "extract": "Business magnate.",
            "content_url": "https://en.wikipedia.org/wiki/Elon_Musk",
            "thumbnail_url": None,
            "last_updated": "2026-01-01T00:00:00Z",
        }
        registry = _make_registry({"wikipedia"})  # only wikipedia enabled

        with (
            patch("app.services.external_apis.wiki_enrichment.WikipediaAdapter") as MockWiki,
            patch("app.services.external_apis.wiki_enrichment.WikidataAdapter"),
        ):
            instance = MockWiki.return_value
            instance.lookup = AsyncMock(return_value=wiki_result)

            refs = await enrich(
                "Elon Musk",
                "person",
                registry=registry,
            )

        assert len(refs) == 1
        assert refs[0].source == "wikipedia"
        assert refs[0].title == "Elon Musk"
        assert "Business magnate" in refs[0].snippet

    async def test_repository_dispatches_github(self):
        """For a repository entity, GitHub adapter is called with owner/repo."""
        github_result = {
            "full_name": "Cerid-AI/cerid-ai",
            "description": "AI knowledge companion",
            "url": "https://github.com/Cerid-AI/cerid-ai",
            "stars": 42,
            "forks": 5,
            "language": "Python",
            "topics": ["ai"],
            "open_issues": 3,
            "pushed_at": "2026-01-01T00:00:00Z",
        }
        registry = _make_registry({"github"})

        with patch("app.services.external_apis.wiki_enrichment.GitHubAdapter") as MockGH:
            instance = MockGH.return_value
            instance.lookup = AsyncMock(return_value=github_result)

            refs = await enrich(
                "Cerid-AI/cerid-ai",
                "repository",
                registry=registry,
            )

        assert len(refs) == 1
        assert refs[0].source == "github"
        assert refs[0].url == "https://github.com/Cerid-AI/cerid-ai"

    async def test_package_pypi_dispatches_packages_with_pypi_ecosystem(self):
        pypi_result = {
            "name": "httpx",
            "version": "0.27.0",
            "summary": "HTTP client.",
            "author": "Tom Christie",
            "license": "BSD",
            "home_page": "https://www.python-httpx.org",
            "requires_python": ">=3.8",
        }
        registry = _make_registry({"packages"})

        with patch("app.services.external_apis.wiki_enrichment.PackagesAdapter") as MockPkg:
            instance = MockPkg.return_value
            instance.lookup = AsyncMock(return_value=pypi_result)

            refs = await enrich(
                "httpx",
                "package_pypi",
                registry=registry,
            )

        assert len(refs) == 1
        assert refs[0].source == "packages"
        # lookup should have been called with pypi ecosystem
        instance.lookup.assert_called_once_with("httpx", ecosystem="pypi")

    async def test_package_npm_dispatches_packages_with_npm_ecosystem(self):
        npm_result = {
            "name": "react",
            "version": "18.2.0",
            "description": "UI library",
            "author": "Meta",
            "license": "MIT",
            "homepage": "https://reactjs.org/",
            "keywords": ["react"],
        }
        registry = _make_registry({"packages"})

        with patch("app.services.external_apis.wiki_enrichment.PackagesAdapter") as MockPkg:
            instance = MockPkg.return_value
            instance.lookup = AsyncMock(return_value=npm_result)

            refs = await enrich(
                "react",
                "package_npm",
                registry=registry,
            )

        assert len(refs) == 1
        assert refs[0].source == "packages"
        instance.lookup.assert_called_once_with("react", ecosystem="npm")

    async def test_place_dispatches_osm_then_wikipedia(self):
        osm_result = [
            {
                "place_id": 1,
                "display_name": "Berlin, Germany",
                "lat": "52.52",
                "lon": "13.40",
                "type": "administrative",
                "importance": 0.9,
                "boundingbox": [],
            }
        ]
        wiki_result = {
            "title": "Berlin",
            "extract": "Capital of Germany.",
            "content_url": "https://en.wikipedia.org/wiki/Berlin",
            "thumbnail_url": None,
            "last_updated": "2026-01-01T00:00:00Z",
        }
        registry = _make_registry({"osm", "wikipedia"})

        with (
            patch("app.services.external_apis.wiki_enrichment.OSMAdapter") as MockOSM,
            patch("app.services.external_apis.wiki_enrichment.WikipediaAdapter") as MockWiki,
        ):
            MockOSM.return_value.lookup = AsyncMock(return_value=osm_result)
            MockWiki.return_value.lookup = AsyncMock(return_value=wiki_result)

            refs = await enrich("Berlin", "place", registry=registry)

        sources = {r.source for r in refs}
        assert "osm" in sources
        assert "wikipedia" in sources


# ---------------------------------------------------------------------------
# enrich() — disabled adapters
# ---------------------------------------------------------------------------


class TestEnrichDisabledAdapters:
    async def test_disabled_adapter_is_skipped(self):
        """When all adapters are disabled, returns empty list."""
        registry = _all_disabled_registry()

        refs = await enrich("Elon Musk", "person", registry=registry)

        assert refs == []
        assert registry.is_enabled.call_count >= 1

    async def test_partially_disabled_only_calls_enabled(self):
        """When wikidata is disabled for a person, only wikipedia is called."""
        registry = _make_registry({"wikipedia"})  # wikidata disabled

        wiki_result = {
            "title": "Alan Turing",
            "extract": "Mathematician.",
            "content_url": "https://en.wikipedia.org/wiki/Alan_Turing",
            "thumbnail_url": None,
            "last_updated": "2026-01-01T00:00:00Z",
        }

        with (
            patch("app.services.external_apis.wiki_enrichment.WikipediaAdapter") as MockWiki,
            patch("app.services.external_apis.wiki_enrichment.WikidataAdapter") as MockWikidata,
        ):
            MockWiki.return_value.lookup = AsyncMock(return_value=wiki_result)
            MockWikidata.return_value.lookup = AsyncMock(return_value={})

            refs = await enrich("Alan Turing", "person", registry=registry)

        # Only wikipedia result
        assert len(refs) == 1
        assert refs[0].source == "wikipedia"
        # Wikidata lookup should NOT have been called
        MockWikidata.return_value.lookup.assert_not_called()


# ---------------------------------------------------------------------------
# enrich() — adapter failure resilience
# ---------------------------------------------------------------------------


class TestEnrichFailureResilience:
    async def test_failing_adapter_does_not_block_others(self):
        """When the first adapter raises, subsequent adapters still run."""
        from app.services.external_apis.base import ExternalAPIError

        registry = _make_registry({"osm", "wikipedia"})

        wiki_result = {
            "title": "Paris",
            "extract": "Capital of France.",
            "content_url": "https://en.wikipedia.org/wiki/Paris",
            "thumbnail_url": None,
            "last_updated": "2026-01-01T00:00:00Z",
        }

        with (
            patch("app.services.external_apis.wiki_enrichment.OSMAdapter") as MockOSM,
            patch("app.services.external_apis.wiki_enrichment.WikipediaAdapter") as MockWiki,
            patch("app.services.external_apis.wiki_enrichment.log_swallowed_error") as mock_log,
        ):
            # OSM raises; wikipedia succeeds
            MockOSM.return_value.lookup = AsyncMock(
                side_effect=ExternalAPIError("osm", "timeout", 0)
            )
            MockWiki.return_value.lookup = AsyncMock(return_value=wiki_result)

            refs = await enrich("Paris", "place", registry=registry)

        # Wikipedia result should still be present
        assert any(r.source == "wikipedia" for r in refs)
        # Error should have been logged (not re-raised)
        mock_log.assert_called()

    async def test_failure_is_logged_not_raised(self):
        """An adapter failure is passed to log_swallowed_error, not raised."""
        registry = _make_registry({"wikipedia"})

        with (
            patch("app.services.external_apis.wiki_enrichment.WikipediaAdapter") as MockWiki,
            patch("app.services.external_apis.wiki_enrichment.log_swallowed_error") as mock_log,
        ):
            MockWiki.return_value.lookup = AsyncMock(side_effect=RuntimeError("network down"))

            # Should not raise
            refs = await enrich("SomeEntity", "unknown", registry=registry)

        assert refs == []
        mock_log.assert_called_once()
        # Confirm the log key contains the adapter slug
        call_args = mock_log.call_args[0]
        assert "wikipedia" in call_args[0]

    async def test_registry_is_enabled_failure_skips_adapter(self):
        """When registry.is_enabled raises, that adapter is skipped silently."""
        registry = MagicMock()
        registry.is_enabled.side_effect = RuntimeError("redis down")

        with patch("app.services.external_apis.wiki_enrichment.log_swallowed_error"):
            refs = await enrich("SomeEntity", "unknown", registry=registry)

        assert refs == []


# ---------------------------------------------------------------------------
# ExternalReference model
# ---------------------------------------------------------------------------


class TestExternalReferenceModel:
    def test_instantiation(self):
        ref = ExternalReference(
            source="wikipedia",
            source_display="Wikipedia",
            title="Python",
            snippet="A programming language.",
            url="https://en.wikipedia.org/wiki/Python",
            fetched_at="2026-05-10T00:00:00+00:00",
        )
        assert ref.source == "wikipedia"
        assert ref.metadata == {}

    def test_metadata_defaults_empty(self):
        ref = ExternalReference(
            source="osm",
            source_display="OpenStreetMap",
            title="Berlin",
            snippet="lat: 52.52, lon: 13.40",
            fetched_at="2026-05-10T00:00:00+00:00",
        )
        assert ref.url is None
        assert ref.metadata == {}

    def test_snippet_stores_as_provided(self):
        long_snip = "x" * 250
        ref = ExternalReference(
            source="wikipedia",
            source_display="Wikipedia",
            title="Long",
            snippet=long_snip,
            fetched_at="2026-05-10T00:00:00+00:00",
        )
        # Model accepts any string; truncation is the orchestrator's responsibility
        assert len(ref.snippet) == 250
