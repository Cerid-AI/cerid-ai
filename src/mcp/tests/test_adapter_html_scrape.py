# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``core.knowledge.adapter_html_scrape``.

DI-driven HTTP getter — tests pass HTML/XML strings directly instead
of touching the network.
"""
from __future__ import annotations

import pytest

from core.knowledge.adapter_html_scrape import (
    HtmlScrapeAdapter,
    HtmlScrapeConfig,
    extract_html_content,
    parse_sitemap_urls,
)
from core.knowledge.adapters import (
    fetch_for_manifest,
    get_adapter,
    list_registered_adapters,
    register_adapter,
)
from core.knowledge.packs import BuildSpec, PackError, PackManifest


def _make_manifest(*, build_config: dict) -> PackManifest:
    return PackManifest.from_dict({
        "id": "html-fixture",
        "name": "html-fixture",
        "version": "1.0.0",
        "description": "fixture",
        "domain": "finance",
        "license": "CC0-1.0",
        "provenance": {"source": "https://www.consumerfinance.gov/ask-cfpb/"},
        "build": {"adapter": "html_scrape", "config": build_config},
    })


# ── Sitemap parser ─────────────────────────────────────────────────────

def test_parse_sitemap_urls_returns_loc_values():
    xml = """<?xml version='1.0'?>
<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
  <url><loc>https://x/y</loc></url>
  <url><loc>https://x/z</loc></url>
</urlset>"""
    assert parse_sitemap_urls(xml) == ["https://x/y", "https://x/z"]


def test_parse_sitemap_urls_handles_index_format():
    """Sitemap-index (nested sitemaps) should expose the inner <loc>s."""
    xml = """<?xml version='1.0'?>
<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
  <sitemap><loc>https://x/sitemap-1.xml</loc></sitemap>
  <sitemap><loc>https://x/sitemap-2.xml</loc></sitemap>
</sitemapindex>"""
    assert parse_sitemap_urls(xml) == [
        "https://x/sitemap-1.xml",
        "https://x/sitemap-2.xml",
    ]


def test_parse_sitemap_urls_raises_on_malformed():
    with pytest.raises(PackError, match="sitemap parse failed"):
        parse_sitemap_urls("<<not xml>>")


# ── Content extractor ──────────────────────────────────────────────────

def test_extract_html_content_strips_default_noise_tags():
    html = """<html><body>
<nav>nav stuff</nav>
<header>header stuff</header>
<main><h1>Real Title</h1><p>Body paragraph one.</p>
<p>Body paragraph two.</p></main>
<footer>footer stuff</footer>
<script>console.log('x')</script>
</body></html>"""
    title, text = extract_html_content(html)
    assert title == "Real Title"
    assert "Body paragraph one." in text
    assert "Body paragraph two." in text
    assert "nav stuff" not in text
    assert "footer stuff" not in text
    assert "console.log" not in text


def test_extract_html_content_with_content_selector():
    """`content_selector` restricts text capture to a subtree."""
    html = """<html><body>
<aside><p>sidebar gunk</p></aside>
<main id="main-content"><h1>Hello</h1><p>Real content.</p></main>
<div class="related"><p>Related links.</p></div>
</body></html>"""
    title, text = extract_html_content(
        html, content_selector="main#main-content",
    )
    assert title == "Hello"
    assert "Real content." in text
    assert "Related links." not in text


def test_extract_html_content_falls_back_to_document_title_tag():
    html = """<html><head><title>Doc Title</title></head>
<body><p>No h1 here.</p></body></html>"""
    title, _ = extract_html_content(html, title_tag="h1")
    assert title == "Doc Title"


def test_extract_html_content_h1_beats_document_title():
    html = """<html><head><title>Doc Title</title></head>
<body><h1>H1 Wins</h1><p>Body.</p></body></html>"""
    title, _ = extract_html_content(html, title_tag="h1")
    assert title == "H1 Wins"


def test_extract_html_content_collapses_whitespace():
    html = """<html><body><p>A   long\n  paragraph\twith\n\nbreaks.</p>
<p>Second.</p></body></html>"""
    _, text = extract_html_content(html)
    # Intra-line whitespace collapsed; paragraph break preserved.
    assert "A long paragraph with" in text or "A long paragraph" in text


# ── Config validation ─────────────────────────────────────────────────

def test_config_requires_exactly_one_discovery_mode():
    with pytest.raises(PackError, match="exactly one"):
        HtmlScrapeConfig.from_build(BuildSpec(
            adapter="html_scrape",
            config={
                "sitemap_url": "https://x/sitemap.xml",
                "url_list": ["https://x/y"],
            },
        ))
    with pytest.raises(PackError, match="exactly one"):
        HtmlScrapeConfig.from_build(BuildSpec(adapter="html_scrape", config={}))


def test_config_requires_https_sitemap():
    with pytest.raises(PackError, match="must be https"):
        HtmlScrapeConfig.from_build(BuildSpec(
            adapter="html_scrape",
            config={"sitemap_url": "http://insecure/sitemap.xml"},
        ))


def test_config_requires_https_url_list_entries():
    with pytest.raises(PackError, match="url_list entries must be https"):
        HtmlScrapeConfig.from_build(BuildSpec(
            adapter="html_scrape",
            config={"url_list": ["http://insecure/x"]},
        ))


def test_config_validates_content_selector_shape():
    with pytest.raises(PackError, match="content_selector"):
        HtmlScrapeConfig.from_build(BuildSpec(
            adapter="html_scrape",
            config={
                "url_list": ["https://x/y"],
                "content_selector": "div > p",  # descendant — not allowed
            },
        ))


@pytest.mark.parametrize("selector", ["main", "main#x", "div.content"])
def test_config_accepts_simple_selectors(selector):
    cfg = HtmlScrapeConfig.from_build(BuildSpec(
        adapter="html_scrape",
        config={"url_list": ["https://x/y"], "content_selector": selector},
    ))
    assert cfg.content_selector == selector


# ── Adapter happy paths ─────────────────────────────────────────────────

def _stub_http(responses: dict[str, str]):
    """Return an http_get stub mapping URL → response body."""
    calls: list[str] = []

    def _get(url, ua):
        calls.append(url)
        if url in responses:
            return responses[url]
        raise AssertionError(f"unexpected URL: {url}")

    _get.calls = calls  # type: ignore[attr-defined]
    return _get


def test_adapter_url_list_mode_writes_files(tmp_path):
    pages = {
        "https://www.consumerfinance.gov/ask-cfpb/what-is-a-credit-score/":
            "<html><body><h1>What is a credit score?</h1>"
            "<p>" + "It's a number that lenders look at. " * 30 + "</p></body></html>",
        "https://www.consumerfinance.gov/ask-cfpb/what-is-apr/":
            "<html><body><h1>What is APR?</h1>"
            "<p>" + "Annual percentage rate. " * 30 + "</p></body></html>",
    }
    adapter = HtmlScrapeAdapter(http_get=_stub_http(pages), sleep=lambda _x: None)
    manifest = _make_manifest(build_config={
        "url_list": list(pages.keys()),
        "min_text_chars": 200,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    names = sorted(p.name for p in result.files)
    assert names == ["what-is-a-credit-score.md", "what-is-apr.md"]
    body = (result.content_root / "what-is-a-credit-score.md").read_text()
    assert body.startswith("# What is a credit score?")
    assert "source: https://" in body  # provenance footer


def test_adapter_sitemap_mode_with_url_glob(tmp_path):
    sitemap = """<?xml version='1.0'?>
<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
  <url><loc>https://x.gov/ask-cfpb/q1/</loc></url>
  <url><loc>https://x.gov/news/article-1/</loc></url>
  <url><loc>https://x.gov/ask-cfpb/q2/</loc></url>
</urlset>"""
    pages = {
        "https://x.gov/ask-cfpb/q1/":
            "<html><body><h1>Q1</h1><p>" + "answer one. " * 30 + "</p></body></html>",
        "https://x.gov/ask-cfpb/q2/":
            "<html><body><h1>Q2</h1><p>" + "answer two. " * 30 + "</p></body></html>",
    }
    responses = {"https://x.gov/sitemap.xml": sitemap, **pages}
    adapter = HtmlScrapeAdapter(http_get=_stub_http(responses), sleep=lambda _x: None)
    manifest = _make_manifest(build_config={
        "sitemap_url": "https://x.gov/sitemap.xml",
        "url_glob": "https://x.gov/ask-cfpb/*",
        "min_text_chars": 200,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert sorted(p.name for p in result.files) == ["q1.md", "q2.md"]


def test_adapter_sitemap_mode_excludes_globs(tmp_path):
    sitemap = """<?xml version='1.0'?>
<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
  <url><loc>https://x.gov/ask-cfpb/q1/</loc></url>
  <url><loc>https://x.gov/ask-cfpb/spanish/q1-es/</loc></url>
</urlset>"""
    pages = {
        "https://x.gov/ask-cfpb/q1/":
            "<html><body><h1>Q1</h1><p>" + "english answer. " * 30 + "</p></body></html>",
    }
    adapter = HtmlScrapeAdapter(
        http_get=_stub_http({**pages, "https://x.gov/sitemap.xml": sitemap}),
        sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "sitemap_url": "https://x.gov/sitemap.xml",
        "url_glob": "https://x.gov/ask-cfpb/*",
        "exclude_globs": ["https://x.gov/ask-cfpb/spanish/*"],
        "min_text_chars": 200,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert [p.name for p in result.files] == ["q1.md"]


def test_adapter_host_allowlist_blocks_smuggled_urls(tmp_path):
    """A sitemap that lists a non-host URL must be filtered out."""
    sitemap = """<?xml version='1.0'?>
<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
  <url><loc>https://x.gov/ask-cfpb/q1/</loc></url>
  <url><loc>https://evil.example.com/q-mal/</loc></url>
</urlset>"""
    pages = {
        "https://x.gov/ask-cfpb/q1/":
            "<html><body><h1>Q1</h1><p>" + "answer. " * 30 + "</p></body></html>",
    }
    adapter = HtmlScrapeAdapter(
        http_get=_stub_http({**pages, "https://x.gov/sitemap.xml": sitemap}),
        sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "sitemap_url": "https://x.gov/sitemap.xml",
        "host_allowlist": ["https://x.gov/"],
        "min_text_chars": 200,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert [p.name for p in result.files] == ["q1.md"]


def test_adapter_max_pages_caps_after_filter(tmp_path):
    sitemap_loc = "\n".join(
        f"  <url><loc>https://x.gov/ask-cfpb/q{i}/</loc></url>"
        for i in range(20)
    )
    sitemap = f"""<?xml version='1.0'?>
<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
{sitemap_loc}
</urlset>"""
    pages = {
        f"https://x.gov/ask-cfpb/q{i}/":
            f"<html><body><h1>Q{i}</h1><p>" + f"answer {i}. " * 30 + "</p></body></html>"
        for i in range(20)
    }
    adapter = HtmlScrapeAdapter(
        http_get=_stub_http({**pages, "https://x.gov/sitemap.xml": sitemap}),
        sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "sitemap_url": "https://x.gov/sitemap.xml",
        "min_text_chars": 100,
        "max_pages": 5,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert len(result.files) == 5


def test_adapter_skips_pages_below_min_text(tmp_path):
    pages = {
        "https://x.gov/short/":
            "<html><body><h1>Short</h1><p>tiny</p></body></html>",
        "https://x.gov/long/":
            "<html><body><h1>Long</h1><p>" + "real content. " * 30 + "</p></body></html>",
    }
    adapter = HtmlScrapeAdapter(http_get=_stub_http(pages), sleep=lambda _x: None)
    manifest = _make_manifest(build_config={
        "url_list": list(pages.keys()),
        "min_text_chars": 200,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert [p.name for p in result.files] == ["long.md"]


def test_adapter_continues_when_one_page_fetch_fails(tmp_path):
    """A 404 / network blip on one URL doesn't abort the build."""

    def _flaky_get(url, ua):
        if "boom" in url:
            raise RuntimeError("simulated failure")
        return "<html><body><h1>OK</h1><p>" + "x" * 500 + "</p></body></html>"

    adapter = HtmlScrapeAdapter(http_get=_flaky_get, sleep=lambda _x: None)
    manifest = _make_manifest(build_config={
        "url_list": ["https://x/ok/", "https://x/boom/"],
        "min_text_chars": 100,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert len(result.files) == 1


def test_adapter_raises_when_no_urls_after_filter(tmp_path):
    sitemap = """<?xml version='1.0'?>
<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
  <url><loc>https://x.gov/news/</loc></url>
</urlset>"""
    adapter = HtmlScrapeAdapter(
        http_get=_stub_http({"https://x.gov/sitemap.xml": sitemap}),
        sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "sitemap_url": "https://x.gov/sitemap.xml",
        "url_glob": "https://x.gov/ask-cfpb/*",
    })
    with pytest.raises(PackError, match="zero URLs"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_adapter_path_traversal_via_url_blocked(tmp_path):
    """A page whose title slugifies to nothing dangerous still slugs cleanly."""
    pages = {
        "https://x.gov/page/":
            "<html><body><h1>../../escape</h1><p>" + "x" * 500 + "</p></body></html>",
    }
    adapter = HtmlScrapeAdapter(http_get=_stub_http(pages), sleep=lambda _x: None)
    manifest = _make_manifest(build_config={
        "url_list": list(pages.keys()),
        "min_text_chars": 100,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert result.files[0].name == "escape.md"
    assert (result.content_root / result.files[0]).is_file()


# ── Registry integration ──────────────────────────────────────────────

def test_html_scrape_registered():
    assert "html_scrape" in list_registered_adapters()
    assert isinstance(get_adapter("html_scrape"), HtmlScrapeAdapter)


def test_fetch_for_manifest_routes_html_scrape(tmp_path):
    pages = {
        "https://x/y/": "<html><body><h1>Routed</h1><p>" + "x" * 500 + "</p></body></html>",
    }
    register_adapter(HtmlScrapeAdapter(
        http_get=_stub_http(pages), sleep=lambda _x: None,
    ))
    try:
        manifest = _make_manifest(build_config={
            "url_list": list(pages.keys()),
            "min_text_chars": 100,
        })
        result = fetch_for_manifest(manifest, staging_root=tmp_path)
        assert result.files[0].name == "routed.md"
    finally:
        register_adapter(HtmlScrapeAdapter())
