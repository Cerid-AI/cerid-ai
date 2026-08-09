# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for ``core.knowledge.adapter_gutenberg``.

DI-driven downloader; tests pass plain text directly. No network.
"""
from __future__ import annotations

import pytest

from core.knowledge.adapter_gutenberg import (
    GutenbergAdapter,
    GutenbergBook,
    GutenbergConfig,
    strip_gutenberg_boilerplate,
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
        "id": "gutenberg-fixture",
        "name": "gutenberg-fixture",
        "version": "1.0.0",
        "description": "fixture",
        "domain": "personal",
        "license": "CC0-1.0",
        "provenance": {"source": "https://www.gutenberg.org/"},
        "build": {"adapter": "gutenberg", "config": build_config},
    })


# ── strip_gutenberg_boilerplate ────────────────────────────────────────

def test_strip_keeps_text_between_markers():
    raw = (
        "License header\n"
        "more legal text\n\n"
        "*** START OF THE PROJECT GUTENBERG EBOOK MEDITATIONS ***\n\n"
        "BOOK CONTENT.\n\nMore book content.\n\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK MEDITATIONS ***\n"
        "License footer.\n"
    )
    body = strip_gutenberg_boilerplate(raw)
    assert "License header" not in body
    assert "License footer" not in body
    assert "BOOK CONTENT." in body


def test_strip_handles_missing_markers():
    raw = "Pure body text with no markers.\n" * 10
    assert strip_gutenberg_boilerplate(raw) == raw.strip()


def test_strip_handles_only_start_marker():
    raw = (
        "header\n"
        "*** START OF THE PROJECT GUTENBERG EBOOK X ***\n"
        "body content here.\n"
    )
    body = strip_gutenberg_boilerplate(raw)
    assert "header" not in body
    assert "body content here." in body


def test_strip_is_case_insensitive():
    raw = (
        "header\n"
        "*** start of this project gutenberg ebook X ***\n"
        "body\n"
        "*** END OF THIS PROJECT GUTENBERG EBOOK X ***\n"
        "footer\n"
    )
    assert strip_gutenberg_boilerplate(raw) == "body"


# ── GutenbergBook validation ────────────────────────────────────────

def test_book_requires_id_and_title():
    with pytest.raises(PackError, match="missing/invalid"):
        GutenbergBook.from_dict({"title": "x"})  # missing id
    with pytest.raises(PackError, match="missing title"):
        GutenbergBook.from_dict({"id": 12})


def test_book_id_must_be_int_compatible():
    with pytest.raises(PackError, match="missing/invalid"):
        GutenbergBook.from_dict({"id": "not-a-number", "title": "x"})


# ── GutenbergConfig validation ──────────────────────────────────────

def test_config_requires_books():
    with pytest.raises(PackError, match="non-empty list"):
        GutenbergConfig.from_build(BuildSpec(adapter="gutenberg", config={}))
    with pytest.raises(PackError, match="non-empty list"):
        GutenbergConfig.from_build(BuildSpec(
            adapter="gutenberg", config={"books": []},
        ))


def test_config_requires_https_url_template():
    with pytest.raises(PackError, match="must be https"):
        GutenbergConfig.from_build(BuildSpec(
            adapter="gutenberg",
            config={
                "books": [{"id": 1, "title": "x"}],
                "url_template": "http://insecure/{id}.txt",
            },
        ))


def test_config_requires_id_placeholder():
    with pytest.raises(PackError, match="placeholder"):
        GutenbergConfig.from_build(BuildSpec(
            adapter="gutenberg",
            config={
                "books": [{"id": 1, "title": "x"}],
                "url_template": "https://x/static.txt",
            },
        ))


# ── Adapter behaviour ──────────────────────────────────────────────────

def _stub_downloader(by_url: dict[str, str]):
    calls: list[str] = []

    def _dl(url, ua):
        calls.append(url)
        if url not in by_url:
            raise AssertionError(f"unexpected URL {url}")
        return by_url[url]

    _dl.calls = calls  # type: ignore[attr-defined]
    return _dl


def test_adapter_downloads_each_book_and_strips_boilerplate(tmp_path):
    body_a = (
        "header\n"
        "*** START OF THE PROJECT GUTENBERG EBOOK MEDITATIONS ***\n"
        + ("Marcus Aurelius wrote.\n" * 100)
        + "*** END OF THE PROJECT GUTENBERG EBOOK MEDITATIONS ***\n"
        "footer\n"
    )
    body_b = (
        "*** START OF THE PROJECT GUTENBERG EBOOK WALDEN ***\n"
        + ("Thoreau wrote.\n" * 100)
        + "*** END OF THE PROJECT GUTENBERG EBOOK WALDEN ***\n"
    )
    by_url = {
        "https://www.gutenberg.org/cache/epub/2680/pg2680.txt": body_a,
        "https://www.gutenberg.org/cache/epub/205/pg205.txt": body_b,
    }
    adapter = GutenbergAdapter(downloader=_stub_downloader(by_url), sleep=lambda _x: None)
    manifest = _make_manifest(build_config={
        "books": [
            {"id": 2680, "title": "Meditations", "author": "Marcus Aurelius"},
            {"id": 205, "title": "Walden", "author": "Henry David Thoreau"},
        ],
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    names = sorted(p.name for p in result.files)
    assert names == ["meditations.md", "walden.md"]
    body = (result.content_root / "meditations.md").read_text()
    assert body.startswith("# Meditations\n\n*by Marcus Aurelius*")
    assert "header" not in body
    assert "footer" not in body
    assert "Marcus Aurelius wrote." in body
    assert "source: https://www.gutenberg.org" in body
    assert "Project Gutenberg ID 2680" in body


def test_adapter_skips_failed_downloads(tmp_path):
    def _flaky(url, ua):
        if "BROKEN" in url:
            raise RuntimeError("simulated 404")
        return (
            "*** START OF THE PROJECT GUTENBERG EBOOK X ***\n"
            + ("body content. " * 200)
            + "*** END OF THE PROJECT GUTENBERG EBOOK X ***\n"
        )

    adapter = GutenbergAdapter(
        downloader=_flaky, sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "books": [
            {"id": 1, "title": "Good"},
            {"id": 2, "title": "Bad"},
        ],
        "url_template": "https://x/{id}.txt",
    })
    # Force the bad URL to be the second one
    manifest = _make_manifest(build_config={
        "books": [
            {"id": 1, "title": "Good"},
            {"id": 2, "title": "BROKEN"},
        ],
        "url_template": "https://x/{title}/{id}.txt",  # invalid — needs id only
    })
    # Reset to valid template — and use an URL that contains BROKEN to flag failure
    manifest = _make_manifest(build_config={
        "books": [
            {"id": 1, "title": "Good"},
            {"id": 9999, "title": "BROKEN"},
        ],
        "url_template": "https://x/BROKEN-{id}.txt",
    })

    def _flaky_targeted(url, ua):
        # Treat the first book as success, subsequent BROKEN-* as failure.
        if "9999" in url:
            raise RuntimeError("simulated 404")
        return (
            "*** START OF THE PROJECT GUTENBERG EBOOK X ***\n"
            + ("body content. " * 200)
            + "*** END OF THE PROJECT GUTENBERG EBOOK X ***\n"
        )

    adapter = GutenbergAdapter(downloader=_flaky_targeted, sleep=lambda _x: None)
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert len(result.files) == 1
    assert result.files[0].name == "good.md"


def test_adapter_skips_below_min_text_chars(tmp_path):
    body_short = (
        "*** START OF THE PROJECT GUTENBERG EBOOK X ***\n"
        "tiny.\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK X ***\n"
    )
    body_long = (
        "*** START OF THE PROJECT GUTENBERG EBOOK Y ***\n"
        + ("a lot of text. " * 200)
        + "*** END OF THE PROJECT GUTENBERG EBOOK Y ***\n"
    )
    by_url = {
        "https://www.gutenberg.org/cache/epub/100/pg100.txt": body_short,
        "https://www.gutenberg.org/cache/epub/200/pg200.txt": body_long,
    }
    adapter = GutenbergAdapter(downloader=_stub_downloader(by_url), sleep=lambda _x: None)
    manifest = _make_manifest(build_config={
        "books": [
            {"id": 100, "title": "Short"},
            {"id": 200, "title": "Long"},
        ],
        "min_text_chars": 1000,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert [p.name for p in result.files] == ["long.md"]


def test_adapter_path_traversal_in_title_blocked(tmp_path):
    body = (
        "*** START OF THE PROJECT GUTENBERG EBOOK X ***\n"
        + ("body. " * 200)
        + "*** END OF THE PROJECT GUTENBERG EBOOK X ***\n"
    )
    adapter = GutenbergAdapter(
        downloader=_stub_downloader({
            "https://www.gutenberg.org/cache/epub/1/pg1.txt": body,
        }),
        sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "books": [{"id": 1, "title": "../escape"}],
        "min_text_chars": 100,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert result.files[0].name == "escape.md"
    assert (result.content_root / result.files[0]).is_file()


def test_adapter_raises_when_all_fail(tmp_path):
    def _all_fail(url, ua):
        raise RuntimeError("everything broke")

    adapter = GutenbergAdapter(downloader=_all_fail, sleep=lambda _x: None)
    manifest = _make_manifest(build_config={
        "books": [{"id": 1, "title": "X"}, {"id": 2, "title": "Y"}],
    })
    with pytest.raises(PackError, match="zero books survived"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_adapter_collision_handling_with_counter(tmp_path):
    body = (
        "*** START OF THE PROJECT GUTENBERG EBOOK X ***\n"
        + ("body. " * 200)
        + "*** END OF THE PROJECT GUTENBERG EBOOK X ***\n"
    )
    by_url = {
        f"https://www.gutenberg.org/cache/epub/{i}/pg{i}.txt": body
        for i in (1, 2, 3)
    }
    adapter = GutenbergAdapter(downloader=_stub_downloader(by_url), sleep=lambda _x: None)
    manifest = _make_manifest(build_config={
        "books": [
            {"id": 1, "title": "Same"},
            {"id": 2, "title": "Same"},
            {"id": 3, "title": "Same"},
        ],
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert sorted(p.name for p in result.files) == ["same-1.md", "same-2.md", "same.md"]


# ── Registry integration ──────────────────────────────────────────────

def test_gutenberg_registered():
    assert "gutenberg" in list_registered_adapters()
    assert isinstance(get_adapter("gutenberg"), GutenbergAdapter)


def test_fetch_for_manifest_routes_gutenberg(tmp_path):
    body = (
        "*** START OF THE PROJECT GUTENBERG EBOOK X ***\n"
        + ("body. " * 200)
        + "*** END OF THE PROJECT GUTENBERG EBOOK X ***\n"
    )
    register_adapter(GutenbergAdapter(
        downloader=_stub_downloader({
            "https://www.gutenberg.org/cache/epub/1/pg1.txt": body,
        }),
        sleep=lambda _x: None,
    ))
    try:
        manifest = _make_manifest(build_config={
            "books": [{"id": 1, "title": "Routed"}],
        })
        result = fetch_for_manifest(manifest, staging_root=tmp_path)
        assert result.files[0].name == "routed.md"
    finally:
        register_adapter(GutenbergAdapter())
