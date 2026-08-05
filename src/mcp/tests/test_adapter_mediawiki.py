# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for ``core.knowledge.adapter_mediawiki``.

Adapter is exercised via a fake HTTP getter so tests run with no
network. The fake records call sequences so we can assert pagination,
batching, and rate-limit interactions.
"""
from __future__ import annotations

import pytest

from core.knowledge.adapter_mediawiki import (
    MediaWikiApiAdapter,
    MediaWikiApiConfig,
    wikitext_to_markdown,
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
        "id": "bogleheads-fixture",
        "name": "Bogleheads fixture",
        "version": "1.0.0",
        "description": "fixture",
        "domain": "finance",
        "license": "CC-BY-SA-4.0",
        "provenance": {"source": "https://www.bogleheads.org/wiki/Main_Page"},
        "build": {"adapter": "mediawiki_api", "config": build_config},
    })


# ── Wikitext converter ─────────────────────────────────────────────────

def test_wikitext_headers_levels():
    wt = "==Top==\n===Sub===\n====Sub-sub===="
    md = wikitext_to_markdown(wt)
    assert "## Top" in md
    assert "### Sub" in md
    assert "#### Sub-sub" in md


def test_wikitext_strips_templates():
    wt = "Hello {{cite web|url=https://x}} world {{nested {{template}}}}"
    md = wikitext_to_markdown(wt)
    assert "{{" not in md
    assert "}}" not in md
    assert "Hello" in md
    assert "world" in md


def test_wikitext_strips_refs():
    wt = "Statement<ref>citation</ref> end<ref name='x'/>."
    md = wikitext_to_markdown(wt)
    assert "<ref" not in md
    assert "Statement" in md
    assert "end" in md


def test_wikitext_bold_italic():
    md = wikitext_to_markdown("'''bold''' and ''italic''")
    assert "**bold**" in md
    assert "*italic*" in md


def test_wikitext_internal_links():
    md = wikitext_to_markdown("See [[Bond fund|bond funds]] and [[Index fund]].")
    assert "bond funds" in md and "Index fund" in md
    assert "[[" not in md


def test_wikitext_external_links():
    md = wikitext_to_markdown("[https://example.org/foo example] and [https://x bare]")
    assert "[example](https://example.org/foo)" in md
    assert "[bare](https://x)" in md


def test_wikitext_collapses_blank_runs():
    md = wikitext_to_markdown("a\n\n\n\n\nb")
    assert "a\n\nb" == md


# ── Config validation ──────────────────────────────────────────────────

def test_mw_config_requires_https_host():
    with pytest.raises(PackError, match="must be https"):
        MediaWikiApiConfig.from_build(BuildSpec(
            adapter="mediawiki_api",
            config={"host": "http://insecure.example", "categories": ["x"]},
        ))


def test_mw_config_rejects_unsafe_host():
    with pytest.raises(PackError, match="unsafe characters"):
        MediaWikiApiConfig.from_build(BuildSpec(
            adapter="mediawiki_api",
            config={"host": "https://x/../y", "categories": ["x"]},
        ))


def test_mw_config_requires_categories_or_pages():
    with pytest.raises(PackError, match="categories.*or.*pages"):
        MediaWikiApiConfig.from_build(BuildSpec(
            adapter="mediawiki_api",
            config={"host": "https://www.bogleheads.org"},
        ))


def test_mw_config_caps_page_batch_size():
    with pytest.raises(PackError, match="page_batch_size must be 1..50"):
        MediaWikiApiConfig.from_build(BuildSpec(
            adapter="mediawiki_api",
            config={
                "host": "https://www.bogleheads.org",
                "categories": ["Personal_finance"],
                "page_batch_size": 100,
            },
        ))


def test_mw_config_api_url():
    cfg = MediaWikiApiConfig.from_build(BuildSpec(
        adapter="mediawiki_api",
        config={
            "host": "https://www.bogleheads.org",
            "api_path": "/w/api.php",
            "categories": ["Investing"],
        },
    ))
    assert cfg.api_url == "https://www.bogleheads.org/w/api.php"


# ── Adapter happy path ─────────────────────────────────────────────────

def _fake_getter(responses: list[dict]):
    """Return an http_get stub that pops responses in order; tracks calls."""
    calls = []

    def _get(url, params, user_agent):
        calls.append({"url": url, "params": dict(params), "ua": user_agent})
        return responses.pop(0)

    _get.calls = calls  # type: ignore[attr-defined]
    return _get


def test_mw_adapter_fetches_categories_and_pages(tmp_path):
    """End-to-end happy path: 1 category → 2 titles + 1 explicit page."""
    responses = [
        # categorymembers response
        {
            "query": {
                "categorymembers": [
                    {"title": "Bond fund"},
                    {"title": "Index fund"},
                ],
            },
        },
        # content batch (3 pages: 2 from category + 1 explicit)
        {
            "query": {
                "pages": [
                    {
                        "title": "Bond fund",
                        "revisions": [{
                            "slots": {"main": {"content":
                                "==Bond fund==\nA '''bond fund''' is a "
                                "fund that invests in [[bond]]s. Some text "
                                "of sufficient length to pass the filter " * 5,
                            }},
                        }],
                    },
                    {
                        "title": "Index fund",
                        "revisions": [{
                            "slots": {"main": {"content":
                                "==Index fund==\nAn index fund tracks an "
                                "index. {{stub}} More content here, plenty "
                                "of words and {{cite}} citations." * 5,
                            }},
                        }],
                    },
                    {
                        "title": "Three-fund portfolio",
                        "revisions": [{
                            "slots": {"main": {"content":
                                "==Three-fund portfolio==\nA simple "
                                "asset-allocation strategy combining "
                                "stocks, bonds, and international." * 5,
                            }},
                        }],
                    },
                ],
            },
        },
    ]
    sleeps: list[float] = []
    adapter = MediaWikiApiAdapter(
        http_get=_fake_getter(responses),
        sleep=sleeps.append,
    )
    manifest = _make_manifest(build_config={
        "host": "https://www.bogleheads.org",
        "categories": ["Investing"],
        "pages": ["Three-fund portfolio"],
        "min_text_chars": 100,
        "rate_limit_seconds": 0.5,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    names = sorted(p.name for p in result.files)
    assert names == ["bond-fund.md", "index-fund.md", "three-fund-portfolio.md"]
    body = (result.content_root / "bond-fund.md").read_text()
    assert "## Bond fund" in body
    assert "**bond fund**" in body
    assert "{{" not in body  # template stripped
    # Rate-limit honoured before each of the 2 API calls.
    assert sleeps == [0.5, 0.5]


def test_mw_adapter_paginates_categorymembers(tmp_path):
    """A category with >500 members paginates via cmcontinue."""
    responses = [
        {
            "query": {"categorymembers": [{"title": "Page 1"}]},
            "continue": {"cmcontinue": "page-2"},
        },
        {
            "query": {"categorymembers": [{"title": "Page 2"}]},
        },
        {
            "query": {
                "pages": [
                    {
                        "title": "Page 1",
                        "revisions": [{
                            "slots": {"main": {"content":
                                "Body content for page 1 of sufficient length." * 5,
                            }},
                        }],
                    },
                    {
                        "title": "Page 2",
                        "revisions": [{
                            "slots": {"main": {"content":
                                "Body content for page 2 of sufficient length." * 5,
                            }},
                        }],
                    },
                ],
            },
        },
    ]
    getter = _fake_getter(responses)
    adapter = MediaWikiApiAdapter(http_get=getter, sleep=lambda _x: None)
    manifest = _make_manifest(build_config={
        "host": "https://www.bogleheads.org",
        "categories": ["Big_category"],
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert len(result.files) == 2
    # First call paginates; second uses cmcontinue.
    assert getter.calls[0]["params"].get("cmcontinue") is None
    assert getter.calls[1]["params"]["cmcontinue"] == "page-2"


def test_mw_adapter_batches_content_fetch(tmp_path):
    """page_batch_size respected when there are more titles than the batch."""
    titles = [{"title": f"Page {i}"} for i in range(7)]
    pages = [
        {
            "title": f"Page {i}",
            "revisions": [{
                "slots": {"main": {"content":
                    f"Body for Page {i} long enough to clear the filter." * 5,
                }},
            }],
        } for i in range(7)
    ]
    responses = [
        {"query": {"categorymembers": titles}},
        {"query": {"pages": pages[:3]}},
        {"query": {"pages": pages[3:6]}},
        {"query": {"pages": pages[6:]}},
    ]
    getter = _fake_getter(responses)
    adapter = MediaWikiApiAdapter(http_get=getter, sleep=lambda _x: None)
    manifest = _make_manifest(build_config={
        "host": "https://www.bogleheads.org",
        "categories": ["Many"],
        "page_batch_size": 3,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert len(result.files) == 7
    # 1 enum call + 3 content batches = 4 total
    assert len(getter.calls) == 4


def test_mw_adapter_skips_below_min_text_chars(tmp_path):
    responses = [
        {"query": {"categorymembers": [{"title": "Tiny"}, {"title": "Big"}]}},
        {
            "query": {
                "pages": [
                    {
                        "title": "Tiny",
                        "revisions": [{"slots": {"main": {"content": "tiny"}}}],
                    },
                    {
                        "title": "Big",
                        "revisions": [{"slots": {"main": {"content":
                            "Long enough body to clear the filter." * 20,
                        }}}],
                    },
                ],
            },
        },
    ]
    adapter = MediaWikiApiAdapter(
        http_get=_fake_getter(responses), sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "host": "https://www.bogleheads.org",
        "categories": ["X"],
        "min_text_chars": 200,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert [p.name for p in result.files] == ["big.md"]


def test_mw_adapter_max_pages_caps_enumeration(tmp_path):
    titles = [{"title": f"P{i}"} for i in range(20)]
    # Even though the cat has 20, the adapter caps at max_pages=3 and
    # only fetches content for those — so the batch request only needs 3.
    pages = [
        {
            "title": f"P{i}",
            "revisions": [{"slots": {"main": {"content":
                f"Body P{i} long enough to clear the filter." * 5,
            }}}],
        }
        for i in range(3)
    ]
    responses = [
        {"query": {"categorymembers": titles}},
        {"query": {"pages": pages}},
    ]
    adapter = MediaWikiApiAdapter(
        http_get=_fake_getter(responses), sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "host": "https://www.bogleheads.org",
        "categories": ["Many"],
        "max_pages": 3,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert len(result.files) == 3


def test_mw_adapter_dedups_overlapping_categories_and_pages(tmp_path):
    """A title resolved by multiple categories (or already in `pages`) appears once."""
    responses = [
        # categorymembers for Investing
        {"query": {"categorymembers": [{"title": "Bond fund"}, {"title": "Index fund"}]}},
        # categorymembers for Personal_finance — overlaps Index fund
        {"query": {"categorymembers": [{"title": "Index fund"}, {"title": "Budget"}]}},
        # content batch for the 4 unique titles
        {
            "query": {
                "pages": [
                    {
                        "title": t,
                        "revisions": [{"slots": {"main": {"content":
                            f"Body {t} of sufficient length to pass." * 5,
                        }}}],
                    }
                    for t in ("Bond fund", "Index fund", "Budget", "Custom page")
                ],
            },
        },
    ]
    adapter = MediaWikiApiAdapter(
        http_get=_fake_getter(responses), sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "host": "https://www.bogleheads.org",
        "categories": ["Investing", "Personal_finance"],
        "pages": ["Custom page", "Bond fund"],  # explicit page also overlaps
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    # 4 unique titles → 4 files (Bond fund / Index fund / Budget / Custom page)
    assert len(result.files) == 4


def test_mw_adapter_raises_when_no_titles(tmp_path):
    responses = [{"query": {"categorymembers": []}}]
    adapter = MediaWikiApiAdapter(
        http_get=_fake_getter(responses), sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "host": "https://www.bogleheads.org",
        "categories": ["Empty_category"],
    })
    with pytest.raises(PackError, match="zero titles"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_mw_adapter_raises_when_all_pages_below_threshold(tmp_path):
    responses = [
        {"query": {"categorymembers": [{"title": "Stub"}]}},
        {"query": {"pages": [{
            "title": "Stub",
            "revisions": [{"slots": {"main": {"content": "x"}}}],
        }]}},
    ]
    adapter = MediaWikiApiAdapter(
        http_get=_fake_getter(responses), sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "host": "https://www.bogleheads.org",
        "categories": ["X"],
        "min_text_chars": 1000,
    })
    with pytest.raises(PackError, match="zero pages survived filter"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_mw_adapter_skips_missing_pages(tmp_path):
    """API-returned `missing` / `invalid` page entries are silently skipped."""
    responses = [
        {"query": {"categorymembers": [
            {"title": "Real"}, {"title": "Vanished"},
        ]}},
        {"query": {"pages": [
            {
                "title": "Real",
                "revisions": [{"slots": {"main": {"content":
                    "Real body of sufficient length to pass the filter." * 5,
                }}}],
            },
            {"title": "Vanished", "missing": True},
        ]}},
    ]
    adapter = MediaWikiApiAdapter(
        http_get=_fake_getter(responses), sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "host": "https://www.bogleheads.org",
        "categories": ["X"],
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert [p.name for p in result.files] == ["real.md"]


def test_mw_adapter_sets_user_agent_on_each_call(tmp_path):
    responses = [
        {"query": {"categorymembers": [{"title": "P"}]}},
        {"query": {"pages": [{"title": "P", "revisions": [{
            "slots": {"main": {"content": "Body of sufficient length." * 5}},
        }]}]}},
    ]
    getter = _fake_getter(responses)
    adapter = MediaWikiApiAdapter(http_get=getter, sleep=lambda _x: None)
    manifest = _make_manifest(build_config={
        "host": "https://www.bogleheads.org",
        "categories": ["X"],
        "user_agent": "Custom-UA/2.0",
    })
    adapter.fetch(manifest, staging_root=tmp_path)
    assert all(c["ua"] == "Custom-UA/2.0" for c in getter.calls)


# ── Registry integration ──────────────────────────────────────────────

def test_mediawiki_api_registered():
    assert "mediawiki_api" in list_registered_adapters()
    assert isinstance(get_adapter("mediawiki_api"), MediaWikiApiAdapter)


def test_fetch_for_manifest_routes_mediawiki_api(tmp_path):
    responses = [
        {"query": {"categorymembers": [{"title": "Routed"}]}},
        {"query": {"pages": [{"title": "Routed", "revisions": [{
            "slots": {"main": {"content": "Routed body of sufficient length." * 5}},
        }]}]}},
    ]
    register_adapter(MediaWikiApiAdapter(
        http_get=_fake_getter(responses), sleep=lambda _x: None,
    ))
    try:
        manifest = _make_manifest(build_config={
            "host": "https://www.bogleheads.org",
            "categories": ["X"],
        })
        result = fetch_for_manifest(manifest, staging_root=tmp_path)
        assert result.files[0].name == "routed.md"
    finally:
        register_adapter(MediaWikiApiAdapter())
