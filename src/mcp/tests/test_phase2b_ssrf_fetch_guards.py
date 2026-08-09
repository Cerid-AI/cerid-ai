# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Phase 2b slice 1 — the two previously-unguarded external fetches now route
through the shared SSRF-guarded ``guarded_get``.

Both fetch attacker/user-influenceable URLs:
- ``_verify_against_cited_url`` fetches URLs cited in LLM output.
- ``pkb_ingest_url`` fetches an arbitrary caller-supplied URL.

Before this slice each used a raw ``httpx.AsyncClient(follow_redirects=True)``
with no host validation and no byte cap. A private/internal target must now be
refused before any body is read.
"""
from unittest.mock import patch

import pytest

_PRIVATE = [(2, 1, 6, "", ("169.254.169.254", 0))]  # cloud-metadata endpoint


class TestCitedUrlVerificationGuard:
    @pytest.mark.asyncio
    async def test_internal_cited_url_refused(self):
        """A cited URL resolving to an internal address raises (SSRF guard),
        so the caller falls through to the KB + web-search path."""
        from core.agents.hallucination.verification import _verify_against_cited_url

        with patch("socket.getaddrinfo", return_value=_PRIVATE):
            with pytest.raises(ValueError, match="SSRF guard"):
                await _verify_against_cited_url("the sky is green", "http://metadata.internal/")

    @pytest.mark.asyncio
    async def test_uses_guarded_get(self, monkeypatch):
        """The cited-URL fetch is routed through guarded_get, not a raw client."""
        import core.agents.hallucination.verification as verif

        called = {}

        async def _fake_guarded_get(url, **kwargs):
            called["url"] = url
            called["kwargs"] = kwargs
            raise RuntimeError("short-circuit after routing")

        with patch("core.ingest.sources.safe_fetch.guarded_get", _fake_guarded_get):
            with pytest.raises(RuntimeError, match="short-circuit"):
                await verif._verify_against_cited_url("claim", "https://example.com/a")
        assert called["url"] == "https://example.com/a"
        assert called["kwargs"].get("user_agent") == "cerid-verifier/1"


class TestPkbIngestUrlGuard:
    @pytest.mark.asyncio
    async def test_internal_url_refused(self):
        """pkb_ingest_url against an internal address surfaces InvalidParamsError,
        and never reaches ingestion."""
        from app.mcp_tools.batch import pkb_ingest_url
        from app.tool_registry import InvalidParamsError

        with patch("socket.getaddrinfo", return_value=_PRIVATE):
            with pytest.raises(InvalidParamsError, match="fetch failed"):
                await pkb_ingest_url("http://169.254.169.254/latest/meta-data/")

    @pytest.mark.asyncio
    async def test_scheme_still_validated_first(self):
        """Non-http(s) schemes are rejected before any fetch/guard runs."""
        from app.mcp_tools.batch import pkb_ingest_url
        from app.tool_registry import InvalidParamsError

        with pytest.raises(InvalidParamsError, match="http"):
            await pkb_ingest_url("file:///etc/passwd")


class TestGuardedGetHeaderMerge:
    @pytest.mark.asyncio
    async def test_custom_headers_merged_over_user_agent(self, monkeypatch):
        """guarded_get forwards caller headers (e.g. Accept) while keeping the
        User-Agent — so migrating a call site off a raw client loses nothing."""
        from core.ingest.sources import safe_fetch

        captured: dict = {}

        class _Boom(Exception):
            pass

        class _Client:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def stream(self, *a, **k):
                raise _Boom()

        monkeypatch.setattr(safe_fetch, "assert_fetchable", lambda url: None)
        monkeypatch.setattr("httpx.AsyncClient", _Client)

        with pytest.raises(_Boom):
            await safe_fetch.guarded_get(
                "https://example.com/x",
                user_agent="cerid-test/1",
                headers={"Accept": "text/html"},
            )

        assert captured["headers"]["User-Agent"] == "cerid-test/1"
        assert captured["headers"]["Accept"] == "text/html"


class TestGuardedGetSync:
    """Phase 5 — sync mirror of guarded_get for sync call sites (html_scrape,
    clipboard daemon). Same SSRF guarantees as the async variant."""

    def test_internal_address_refused(self):
        """A URL resolving to an internal address is refused before any fetch."""
        from core.ingest.sources.safe_fetch import guarded_get_sync

        with patch("socket.getaddrinfo", return_value=_PRIVATE):
            with pytest.raises(ValueError, match="SSRF guard"):
                guarded_get_sync("http://metadata.internal/")

    def test_scheme_validated(self):
        from core.ingest.sources.safe_fetch import guarded_get_sync

        with pytest.raises(ValueError, match="scheme"):
            guarded_get_sync("file:///etc/passwd")

    def test_disables_autoredirect(self, monkeypatch):
        """The sync client never delegates redirect-following to httpx."""
        from core.ingest.sources import safe_fetch

        captured: dict = {}

        class _Boom(Exception):
            pass

        class _Client:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def stream(self, *a, **k):
                raise _Boom()

        monkeypatch.setattr(safe_fetch, "assert_fetchable", lambda url: None)
        monkeypatch.setattr("httpx.Client", _Client)

        with pytest.raises(_Boom):
            safe_fetch.guarded_get_sync("https://example.com/x")
        assert captured["follow_redirects"] is False


class TestHtmlScrapeGuard:
    def test_httpx_text_get_uses_guarded_sync(self, monkeypatch):
        """The operator-supplied sitemap/page fetch routes through the guard."""
        from core.ingest.sources import safe_fetch

        with patch("socket.getaddrinfo", return_value=_PRIVATE):
            from core.knowledge.adapter_html_scrape import _httpx_text_get

            with pytest.raises(ValueError, match="SSRF guard"):
                _httpx_text_get("http://metadata.internal/", "ua/1")
        assert safe_fetch.guarded_get_sync is not None
