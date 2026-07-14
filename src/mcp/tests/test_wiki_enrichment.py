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


# ---------------------------------------------------------------------------
# is_plausible_wikipedia_title (2026-07-12 junk-title filter)
# ---------------------------------------------------------------------------


class TestIsPlausibleWikipediaTitle:
    def _filter(self, name: str) -> bool:
        from app.services.external_apis.wiki_enrichment import (
            is_plausible_wikipedia_title,
        )
        return is_plausible_wikipedia_title(name)

    # -- rejects -----------------------------------------------------------

    def test_rejects_full_url(self):
        assert self._filter("https://docs.python.org/3/library/asyncio") is False

    def test_rejects_scheme_anywhere(self):
        assert self._filter("see ftp://host/file") is False

    def test_rejects_bare_domain(self):
        assert self._filter("example.com") is False

    def test_rejects_www_prefix(self):
        assert self._filter("www.wikipedia.org") is False

    def test_rejects_domain_with_path(self):
        assert self._filter("docs.python.org/3") is False

    def test_rejects_quarter(self):
        assert self._filter("Q3 2024") is False

    def test_rejects_quarter_year_first(self):
        assert self._filter("2024 Q3") is False

    def test_rejects_bare_year(self):
        assert self._filter("2024") is False

    def test_rejects_iso_date(self):
        assert self._filter("2024-07-12") is False

    def test_rejects_slashed_date(self):
        assert self._filter("7/12/2024") is False

    def test_rejects_month_year(self):
        assert self._filter("March 2024") is False

    def test_rejects_day_month_year(self):
        assert self._filter("15 March 2024") is False

    def test_rejects_cfr_citation(self):
        assert self._filter("5 CFR 1320.3(h)3") is False

    def test_rejects_usc_citation(self):
        assert self._filter("42 U.S.C. 1983") is False

    def test_rejects_section_symbol(self):
        assert self._filter("Section § 230 immunity") is False

    def test_rejects_sentence_like_string(self):
        assert self._filter(
            "the quarterly report shows revenue increased across all segments"
        ) is False

    def test_rejects_empty_and_whitespace(self):
        assert self._filter("") is False
        assert self._filter("   ") is False

    # -- keeps -------------------------------------------------------------

    def test_keeps_plain_name(self):
        assert self._filter("Marie Curie") is True

    def test_keeps_disambiguated_title(self):
        assert self._filter("Python (programming language)") is True

    def test_keeps_dotted_software_name(self):
        # Dotted but not a bare domain — "js" is not a rejected TLD.
        assert self._filter("Node.js") is True

    def test_keeps_multiword_concept_within_limit(self):
        assert self._filter("General Data Protection Regulation") is True

    def test_keeps_title_containing_a_year(self):
        assert self._filter("2024 Summer Olympics") is True


class TestEnrichSkipsImplausibleTitles:
    async def test_wikipedia_not_called_for_junk_title(self):
        registry = _make_registry({"wikipedia"})

        with (
            patch("app.services.external_apis.wiki_enrichment.WikipediaAdapter") as MockWiki,
            patch("app.services.external_apis.wiki_enrichment.WikidataAdapter"),
        ):
            instance = MockWiki.return_value
            instance.lookup = AsyncMock()

            refs = await enrich(
                "https://example.com/report?q=3",
                "unknown",
                registry=registry,
            )

        instance.lookup.assert_not_awaited()
        assert refs == []
        # The junk gate fires before the registry probe — no wasted work.
        registry.is_enabled.assert_not_called()

    async def test_wikipedia_still_called_for_plausible_title(self):
        wiki_result = {
            "title": "Marie Curie",
            "extract": "Physicist and chemist.",
            "content_url": "https://en.wikipedia.org/wiki/Marie_Curie",
            "thumbnail_url": None,
            "last_updated": "2026-01-01T00:00:00Z",
        }
        registry = _make_registry({"wikipedia"})

        with (
            patch("app.services.external_apis.wiki_enrichment.WikipediaAdapter") as MockWiki,
            patch("app.services.external_apis.wiki_enrichment.WikidataAdapter"),
        ):
            instance = MockWiki.return_value
            instance.lookup = AsyncMock(return_value=wiki_result)

            refs = await enrich("Marie Curie", "person", registry=registry)

        instance.lookup.assert_awaited_once()
        assert len(refs) == 1
        assert refs[0].title == "Marie Curie"

    async def test_junk_gate_does_not_block_non_wikipedia_adapters(self):
        """The gate is wikipedia-specific — github lookups for owner/repo
        style names (which look URL-ish to a title filter) must proceed."""
        github_result = {
            "full_name": "Cerid-AI/cerid-ai",
            "description": "AI knowledge companion",
            "url": "https://github.com/Cerid-AI/cerid-ai",
        }
        registry = _make_registry({"github"})

        with patch("app.services.external_apis.wiki_enrichment.GitHubAdapter") as MockGH:
            instance = MockGH.return_value
            instance.lookup = AsyncMock(return_value=github_result)

            refs = await enrich(
                "Cerid-AI/cerid-ai", "repository", registry=registry,
            )

        instance.lookup.assert_awaited_once()
        assert len(refs) == 1


# ---------------------------------------------------------------------------
# Per-route junk gate (2026-07-13) — every adapter route, not just wikipedia
# ---------------------------------------------------------------------------


class TestAdapterGateRejections:
    async def test_github_not_called_for_doc_path_repo(self):
        """'library/email.charset.html' must not be treated as owner/repo."""
        registry = _make_registry({"github"})

        with patch("app.services.external_apis.wiki_enrichment.GitHubAdapter") as MockGH:
            MockGH.return_value.lookup = AsyncMock()

            refs = await enrich(
                "library/email.charset.html", "repository", registry=registry,
            )

        MockGH.return_value.lookup.assert_not_awaited()
        assert refs == []
        # Gate fires before the registry probe — no wasted work.
        registry.is_enabled.assert_not_called()

    async def test_github_not_called_when_owner_segment_is_doc_file(self):
        """Segment-level check: owner ending in a doc extension is rejected
        even when the whole name does not end in one."""
        registry = _make_registry({"github"})

        with patch("app.services.external_apis.wiki_enrichment.GitHubAdapter") as MockGH:
            MockGH.return_value.lookup = AsyncMock()

            refs = await enrich("guide.html/repo", "repository", registry=registry)

        MockGH.return_value.lookup.assert_not_awaited()
        assert refs == []

    async def test_generic_gate_applies_to_non_wikipedia_routes(self):
        """A pure version token is skipped on every route (wikidata too)."""
        registry = _all_enabled_registry()

        with (
            patch("app.services.external_apis.wiki_enrichment.WikipediaAdapter") as MockWiki,
            patch("app.services.external_apis.wiki_enrichment.WikidataAdapter") as MockWikidata,
        ):
            MockWiki.return_value.lookup = AsyncMock()
            MockWikidata.return_value.lookup = AsyncMock()

            refs = await enrich("v3.6.1", "concept", registry=registry)

        MockWiki.return_value.lookup.assert_not_awaited()
        MockWikidata.return_value.lookup.assert_not_awaited()
        assert refs == []

    @pytest.mark.parametrize("name", ["ALIASES", "CHARSETS"])
    async def test_wikipedia_skips_shouty_unknown_tokens(self, name):
        registry = _make_registry({"wikipedia"})

        with patch("app.services.external_apis.wiki_enrichment.WikipediaAdapter") as MockWiki:
            MockWiki.return_value.lookup = AsyncMock()

            refs = await enrich(name, "unknown", registry=registry)

        MockWiki.return_value.lookup.assert_not_awaited()
        assert refs == []

    @pytest.mark.parametrize("name", ["euc-jp", "iso-2022-jp", "utf-8"])
    async def test_wikipedia_skips_codec_alias_unknown_tokens(self, name):
        registry = _make_registry({"wikipedia"})

        with patch("app.services.external_apis.wiki_enrichment.WikipediaAdapter") as MockWiki:
            MockWiki.return_value.lookup = AsyncMock()

            refs = await enrich(name, "unknown", registry=registry)

        MockWiki.return_value.lookup.assert_not_awaited()
        assert refs == []


class TestAdapterGateAdmissions:
    @staticmethod
    def _wiki_result(title: str) -> dict:
        return {
            "title": title,
            "extract": f"About {title}.",
            "content_url": f"https://en.wikipedia.org/wiki/{title}",
            "thumbnail_url": None,
            "last_updated": "2026-01-01T00:00:00Z",
        }

    @pytest.mark.parametrize("name", ["NASA", "IBM", "gpt-4", "scikit-learn", "Node.js"])
    async def test_wikipedia_called_for_valid_unknown_entities(self, name):
        """Short acronyms and hyphen/dot software names must still enrich."""
        registry = _make_registry({"wikipedia"})

        with patch("app.services.external_apis.wiki_enrichment.WikipediaAdapter") as MockWiki:
            MockWiki.return_value.lookup = AsyncMock(return_value=self._wiki_result(name))

            refs = await enrich(name, "unknown", registry=registry)

        MockWiki.return_value.lookup.assert_awaited_once()
        assert len(refs) == 1
        assert refs[0].title == name

    async def test_shouty_gate_only_applies_to_unknown_type(self):
        """An ALL-CAPS name with a genuine inferred type is never blocked."""
        registry = _make_registry({"wikipedia"})

        with (
            patch("app.services.external_apis.wiki_enrichment.WikipediaAdapter") as MockWiki,
            patch("app.services.external_apis.wiki_enrichment.WikidataAdapter"),
        ):
            MockWiki.return_value.lookup = AsyncMock(return_value=self._wiki_result("UNIVERSE"))

            refs = await enrich("UNIVERSE", "concept", registry=registry)

        MockWiki.return_value.lookup.assert_awaited_once()
        assert len(refs) == 1
