# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Per-adapter unit tests (Phase API.1).

For each of the eight adapters:
* Construction works and slug/display_name are correct.
* Primary method succeeds when HTTP returns 200 with valid JSON/XML.
* Error path: non-2xx raises ExternalAPIError.
* health_check() returns bool from a mocked HTTP call.

All HTTP is mocked via respx — NO real network calls.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from app.services.external_apis.arxiv import ArxivAdapter
from app.services.external_apis.base import ExternalAPIError, close_http_client
from app.services.external_apis.github import GitHubAdapter
from app.services.external_apis.openlibrary import OpenLibraryAdapter
from app.services.external_apis.osm import OSMAdapter
from app.services.external_apis.packages import PackagesAdapter
from app.services.external_apis.stackexchange import StackExchangeAdapter
from app.services.external_apis.wikidata import WikidataAdapter
from app.services.external_apis.wikipedia import WikipediaAdapter

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Shared fixture: reset the singleton HTTP client before every test so
# respx can intercept it cleanly.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _reset_http_client():
    await close_http_client()
    yield
    await close_http_client()


# ---------------------------------------------------------------------------
# Wikipedia
# ---------------------------------------------------------------------------


class TestWikipediaAdapter:
    def test_slug_and_display_name(self):
        a = WikipediaAdapter()
        assert a.slug == "wikipedia"
        assert a.display_name == "Wikipedia"
        assert a.requires_key is False

    @respx.mock
    async def test_lookup_success(self):
        respx.get("https://en.wikipedia.org/api/rest_v1/page/summary/Python").mock(
            return_value=httpx.Response(
                200,
                json={
                    "title": "Python",
                    "extract": "A programming language.",
                    "thumbnail": {"source": "https://example.com/img.jpg"},
                    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Python"}},
                    "timestamp": "2026-01-01T00:00:00Z",
                },
            )
        )
        a = WikipediaAdapter()
        result = await a.lookup("Python")
        assert result["title"] == "Python"
        assert result["extract"] == "A programming language."
        assert result["thumbnail_url"] == "https://example.com/img.jpg"
        assert result["content_url"] == "https://en.wikipedia.org/wiki/Python"
        assert result["last_updated"] == "2026-01-01T00:00:00Z"

    @respx.mock
    async def test_lookup_error_raises_external_api_error(self):
        respx.get("https://en.wikipedia.org/api/rest_v1/page/summary/NONEXISTENT").mock(
            return_value=httpx.Response(500, json={"error": "internal"})
        )
        with patch("app.services.external_apis.base.log_swallowed_error"):
            with pytest.raises(ExternalAPIError) as exc_info:
                await WikipediaAdapter().lookup("NONEXISTENT")
        assert exc_info.value.status_code == 500
        assert exc_info.value.provider == "wikipedia"

    @respx.mock
    async def test_health_check_ok(self):
        respx.get("https://en.wikipedia.org/api/rest_v1/page/summary/Cerid_AI").mock(
            return_value=httpx.Response(404, json={})  # 404 is fine
        )
        ok = await WikipediaAdapter().health_check()
        assert ok is True

    @respx.mock
    async def test_health_check_error_on_500(self):
        respx.get("https://en.wikipedia.org/api/rest_v1/page/summary/Cerid_AI").mock(
            return_value=httpx.Response(503)
        )
        ok = await WikipediaAdapter().health_check()
        assert ok is False


# ---------------------------------------------------------------------------
# Wikidata
# ---------------------------------------------------------------------------


class TestWikidataAdapter:
    def test_slug_and_display_name(self):
        a = WikidataAdapter()
        assert a.slug == "wikidata"
        assert a.display_name == "Wikidata"
        assert a.requires_key is False

    @respx.mock
    async def test_sparql_success(self):
        payload = {
            "results": {
                "bindings": [
                    {"x": {"type": "literal", "value": "1"}}
                ]
            }
        }
        respx.post("https://query.wikidata.org/sparql").mock(
            return_value=httpx.Response(200, json=payload)
        )
        results = await WikidataAdapter().sparql("SELECT ?x WHERE { BIND(1 AS ?x) } LIMIT 1")
        assert len(results) == 1
        assert results[0]["x"]["value"] == "1"

    @respx.mock
    async def test_lookup_success(self):
        payload = {
            "entities": {
                "Q42": {
                    "labels": {"en": {"value": "Douglas Adams"}},
                    "descriptions": {"en": {"value": "English author"}},
                    "claims": {"P31": [], "P21": []},
                }
            }
        }
        respx.get("https://www.wikidata.org/wiki/Special:EntityData/Q42.json").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await WikidataAdapter().lookup("Q42")
        assert result["label"] == "Douglas Adams"
        assert result["description"] == "English author"
        assert result["claims_count"] == 2

    @respx.mock
    async def test_lookup_error_raises(self):
        respx.get("https://www.wikidata.org/wiki/Special:EntityData/Q99999.json").mock(
            return_value=httpx.Response(404)
        )
        with patch("app.services.external_apis.base.log_swallowed_error"):
            with pytest.raises(ExternalAPIError) as exc_info:
                await WikidataAdapter().lookup("Q99999")
        assert exc_info.value.provider == "wikidata"

    @respx.mock
    async def test_health_check_ok(self):
        respx.get("https://query.wikidata.org/sparql").mock(
            return_value=httpx.Response(200, json={"results": {"bindings": [{"x": {"type": "literal", "value": "1"}}]}})
        )
        ok = await WikidataAdapter().health_check()
        assert ok is True


# ---------------------------------------------------------------------------
# Open Library
# ---------------------------------------------------------------------------


class TestOpenLibraryAdapter:
    def test_slug_and_display_name(self):
        a = OpenLibraryAdapter()
        assert a.slug == "openlibrary"
        assert a.display_name == "Open Library"
        assert a.requires_key is False

    @respx.mock
    async def test_lookup_by_isbn_success(self):
        payload = {
            "title": "The Hitchhiker's Guide to the Galaxy",
            "authors": [{"author": {"key": "/authors/OL25169A"}}],
            "first_publish_date": "1979",
            "publishers": ["Pan Books"],
            "subjects": ["Science fiction", "Humour"],
            "description": "A comedy sci-fi novel.",
            "covers": [8090263],
        }
        respx.get("https://openlibrary.org/isbn/9780330258647.json").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await OpenLibraryAdapter().lookup("9780330258647")
        assert result["title"] == "The Hitchhiker's Guide to the Galaxy"
        assert "cover_url" in result
        assert result["cover_url"] is not None

    @respx.mock
    async def test_search_success(self):
        payload = {
            "docs": [
                {"key": "/works/OL45804W", "title": "Dune", "author_name": ["Frank Herbert"], "first_publish_year": 1965}
            ]
        }
        respx.get("https://openlibrary.org/search.json").mock(
            return_value=httpx.Response(200, json=payload)
        )
        results = await OpenLibraryAdapter().search("Dune")
        assert len(results) == 1
        assert results[0]["title"] == "Dune"

    @respx.mock
    async def test_lookup_error_raises(self):
        respx.get("https://openlibrary.org/isbn/0000000000.json").mock(
            return_value=httpx.Response(404)
        )
        with patch("app.services.external_apis.base.log_swallowed_error"):
            with pytest.raises(ExternalAPIError):
                await OpenLibraryAdapter().lookup("0000000000")

    @respx.mock
    async def test_health_check_ok(self):
        respx.get("https://openlibrary.org/isbn/9780140449136.json").mock(
            return_value=httpx.Response(200, json={"title": "The Iliad"})
        )
        ok = await OpenLibraryAdapter().health_check()
        assert ok is True


# ---------------------------------------------------------------------------
# Stack Exchange
# ---------------------------------------------------------------------------


class TestStackExchangeAdapter:
    def test_slug_and_display_name(self):
        a = StackExchangeAdapter()
        assert a.slug == "stackexchange"
        assert a.display_name == "Stack Exchange"
        assert a.requires_key is False

    @respx.mock
    async def test_search_success(self):
        payload = {
            "items": [
                {
                    "title": "How to reverse a list in Python?",
                    "link": "https://stackoverflow.com/q/1",
                    "score": 500,
                    "answer_count": 3,
                    "tags": ["python", "list"],
                    "is_answered": True,
                }
            ]
        }
        respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
            return_value=httpx.Response(200, json=payload)
        )
        results = await StackExchangeAdapter().search("reverse list python", pagesize=5)
        assert len(results) == 1
        assert results[0]["score"] == 500
        assert results[0]["is_answered"] is True

    @respx.mock
    async def test_search_error_raises(self):
        respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
            return_value=httpx.Response(429)
        )
        with patch("app.services.external_apis.base.log_swallowed_error"):
            with pytest.raises(ExternalAPIError) as exc_info:
                await StackExchangeAdapter().search("python")
        assert exc_info.value.provider == "stackexchange"

    @respx.mock
    async def test_health_check_ok(self):
        respx.get("https://api.stackexchange.com/2.3/info").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        ok = await StackExchangeAdapter().health_check()
        assert ok is True

    @respx.mock
    async def test_lookup_delegates_to_search(self):
        payload = {"items": []}
        respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await StackExchangeAdapter().lookup("asyncio")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------

_ARXIV_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2301.00001v1</id>
    <title>Attention Is All You Need</title>
    <summary>A model that uses attention.</summary>
    <published>2023-01-01T00:00:00Z</published>
    <author><name>Vaswani et al.</name></author>
    <link title="pdf" href="https://arxiv.org/pdf/2301.00001"/>
    <category term="cs.LG"/>
  </entry>
</feed>"""


class TestArxivAdapter:
    def test_slug_and_display_name(self):
        a = ArxivAdapter()
        assert a.slug == "arxiv"
        assert a.display_name == "arXiv"
        assert a.requires_key is False

    @respx.mock
    async def test_search_success(self):
        respx.get("http://export.arxiv.org/api/query").mock(
            return_value=httpx.Response(200, text=_ARXIV_ATOM, headers={"content-type": "application/atom+xml"})
        )
        results = await ArxivAdapter().search("attention mechanism")
        assert len(results) == 1
        assert results[0]["title"] == "Attention Is All You Need"
        assert "2301.00001" in results[0]["arxiv_id"]
        assert results[0]["pdf_url"] == "https://arxiv.org/pdf/2301.00001"
        assert "cs.LG" in results[0]["categories"]

    @respx.mock
    async def test_search_error_raises(self):
        respx.get("http://export.arxiv.org/api/query").mock(
            return_value=httpx.Response(503)
        )
        with patch("app.services.external_apis.base.log_swallowed_error"):
            with pytest.raises(ExternalAPIError):
                await ArxivAdapter().search("test")

    @respx.mock
    async def test_search_bad_xml_raises(self):
        respx.get("http://export.arxiv.org/api/query").mock(
            return_value=httpx.Response(200, text="<broken<xml>")
        )
        with pytest.raises(ExternalAPIError) as exc_info:
            await ArxivAdapter().search("test")
        assert "parse" in exc_info.value.detail.lower()

    @respx.mock
    async def test_health_check_ok(self):
        respx.get("http://export.arxiv.org/api/query").mock(
            return_value=httpx.Response(200, text=_ARXIV_ATOM)
        )
        ok = await ArxivAdapter().health_check()
        assert ok is True

    @respx.mock
    async def test_lookup_delegates_to_search(self):
        respx.get("http://export.arxiv.org/api/query").mock(
            return_value=httpx.Response(200, text=_ARXIV_ATOM)
        )
        result = await ArxivAdapter().lookup("attention")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


class TestGitHubAdapter:
    def test_slug_and_display_name(self):
        a = GitHubAdapter()
        assert a.slug == "github"
        assert a.display_name == "GitHub"
        assert a.requires_key is False
        assert a.key_env_var == "GITHUB_TOKEN"

    @respx.mock
    async def test_get_repo_success(self):
        payload = {
            "full_name": "Cerid-AI/cerid-ai",
            "description": "Personal AI knowledge companion",
            "html_url": "https://github.com/Cerid-AI/cerid-ai",
            "stargazers_count": 42,
            "forks_count": 5,
            "language": "Python",
            "topics": ["ai", "knowledge"],
            "open_issues_count": 3,
            "pushed_at": "2026-01-01T00:00:00Z",
        }
        respx.get("https://api.github.com/repos/Cerid-AI/cerid-ai").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await GitHubAdapter().get_repo("Cerid-AI", "cerid-ai")
        assert result["full_name"] == "Cerid-AI/cerid-ai"
        assert result["stars"] == 42
        assert result["language"] == "Python"

    @respx.mock
    async def test_get_user_success(self):
        payload = {
            "login": "torvalds",
            "name": "Linus Torvalds",
            "bio": "Just a guy with a hobby.",
            "html_url": "https://github.com/torvalds",
            "public_repos": 10,
            "followers": 200000,
            "created_at": "2011-09-03T00:00:00Z",
        }
        respx.get("https://api.github.com/users/torvalds").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await GitHubAdapter().get_user("torvalds")
        assert result["login"] == "torvalds"
        assert result["followers"] == 200000

    @respx.mock
    async def test_get_repo_error_raises(self):
        respx.get("https://api.github.com/repos/nonexistent/repo").mock(
            return_value=httpx.Response(404)
        )
        with patch("app.services.external_apis.base.log_swallowed_error"):
            with pytest.raises(ExternalAPIError) as exc_info:
                await GitHubAdapter().get_repo("nonexistent", "repo")
        assert exc_info.value.status_code == 404

    @respx.mock
    async def test_health_check_ok(self):
        respx.get("https://api.github.com/zen").mock(
            return_value=httpx.Response(200, text="Speak like a human.")
        )
        ok = await GitHubAdapter().health_check()
        assert ok is True


# ---------------------------------------------------------------------------
# Packages (PyPI + npm)
# ---------------------------------------------------------------------------


class TestPackagesAdapter:
    def test_slug_and_display_name(self):
        a = PackagesAdapter()
        assert a.slug == "packages"
        assert a.display_name == "PyPI / npm"
        assert a.requires_key is False

    @respx.mock
    async def test_pypi_success(self):
        payload = {
            "info": {
                "name": "httpx",
                "version": "0.27.0",
                "summary": "The next generation HTTP client.",
                "author": "Tom Christie",
                "license": "BSD",
                "home_page": "https://www.python-httpx.org",
                "requires_python": ">=3.8",
            }
        }
        respx.get("https://pypi.org/pypi/httpx/json").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await PackagesAdapter().pypi("httpx")
        assert result["name"] == "httpx"
        assert result["version"] == "0.27.0"
        assert result["license"] == "BSD"

    @respx.mock
    async def test_npm_success(self):
        payload = {
            "name": "react",
            "description": "React is a JavaScript library for building user interfaces.",
            "dist-tags": {"latest": "18.2.0"},
            "versions": {
                "18.2.0": {
                    "license": "MIT",
                    "author": {"name": "Meta Open Source"},
                    "homepage": "https://reactjs.org/",
                }
            },
            "keywords": ["react"],
            "homepage": "https://reactjs.org/",
        }
        respx.get("https://registry.npmjs.org/react").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await PackagesAdapter().npm("react")
        assert result["name"] == "react"
        assert result["version"] == "18.2.0"
        assert result["license"] == "MIT"

    @respx.mock
    async def test_lookup_dispatches_to_pypi(self):
        payload = {"info": {"name": "pip", "version": "24.0", "summary": "PyPA recommended tool", "author": "PyPA", "license": "MIT", "home_page": "", "requires_python": ""}}
        respx.get("https://pypi.org/pypi/pip/json").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await PackagesAdapter().lookup("pip", ecosystem="pypi")
        assert result["name"] == "pip"

    @respx.mock
    async def test_lookup_dispatches_to_npm(self):
        payload = {"name": "lodash", "dist-tags": {"latest": "4.17.21"}, "versions": {"4.17.21": {"license": "MIT", "author": {"name": "John-David Dalton"}}}, "description": "A JS utility library.", "keywords": []}
        respx.get("https://registry.npmjs.org/lodash").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await PackagesAdapter().lookup("lodash", ecosystem="npm")
        assert result["name"] == "lodash"

    async def test_lookup_unknown_ecosystem_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown ecosystem"):
            await PackagesAdapter().lookup("foo", ecosystem="crates")  # type: ignore[arg-type]

    @respx.mock
    async def test_pypi_error_raises(self):
        respx.get("https://pypi.org/pypi/nonexistent-pkg-xyz/json").mock(
            return_value=httpx.Response(404)
        )
        with patch("app.services.external_apis.base.log_swallowed_error"):
            with pytest.raises(ExternalAPIError):
                await PackagesAdapter().pypi("nonexistent-pkg-xyz")

    @respx.mock
    async def test_health_check_ok(self):
        respx.get("https://pypi.org/pypi/pip/json").mock(
            return_value=httpx.Response(200, json={"info": {"name": "pip", "version": "24.0"}})
        )
        ok = await PackagesAdapter().health_check()
        assert ok is True


# ---------------------------------------------------------------------------
# OSM (Nominatim)
# ---------------------------------------------------------------------------


class TestOSMAdapter:
    def test_slug_and_display_name(self):
        a = OSMAdapter()
        assert a.slug == "osm"
        assert a.display_name == "OpenStreetMap Nominatim"
        assert a.requires_key is False

    @respx.mock
    async def test_geocode_success(self):
        payload = [
            {
                "place_id": 12345,
                "display_name": "Berlin, Germany",
                "lat": "52.5200",
                "lon": "13.4050",
                "type": "administrative",
                "importance": 0.9,
                "boundingbox": ["52.3", "52.7", "13.1", "13.8"],
            }
        ]
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=httpx.Response(200, json=payload)
        )
        results = await OSMAdapter().geocode("Berlin, Germany")
        assert len(results) == 1
        assert results[0]["display_name"] == "Berlin, Germany"
        assert results[0]["lat"] == "52.5200"

    @respx.mock
    async def test_reverse_success(self):
        payload = {
            "place_id": 999,
            "display_name": "10 Downing Street, London",
            "address": {"road": "Downing Street", "city": "London"},
            "lat": "51.5034",
            "lon": "-0.1276",
            "type": "place",
        }
        respx.get("https://nominatim.openstreetmap.org/reverse").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await OSMAdapter().reverse(51.5034, -0.1276)
        assert result["display_name"] == "10 Downing Street, London"
        assert "road" in result["address"]

    @respx.mock
    async def test_geocode_error_raises(self):
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=httpx.Response(500)
        )
        with patch("app.services.external_apis.base.log_swallowed_error"):
            with pytest.raises(ExternalAPIError):
                await OSMAdapter().geocode("X")

    @respx.mock
    async def test_health_check_ok(self):
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=httpx.Response(200, json=[{"place_id": 1, "display_name": "London", "lat": "51.5", "lon": "-0.1", "type": "city", "importance": 0.8, "boundingbox": []}])
        )
        ok = await OSMAdapter().health_check()
        assert ok is True

    @respx.mock
    async def test_lookup_delegates_to_geocode(self):
        payload = [{"place_id": 1, "display_name": "Paris", "lat": "48.8", "lon": "2.3", "type": "city", "importance": 0.9, "boundingbox": []}]
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await OSMAdapter().lookup("Paris")
        assert isinstance(result, list)
