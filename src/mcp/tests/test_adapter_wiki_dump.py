# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``core.knowledge.adapter_wiki_dump``.

Tests pass plain XML bytes through the adapter via DI hooks — no
network, no real bz2 decompression (we override ``stream_opener`` to
``open(path, 'rb')``).
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from core.knowledge.adapter_wiki_dump import (
    WikiDumpAdapter,
    WikiDumpConfig,
    iter_pages_from_dump,
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
        "id": "wikivoyage-fixture",
        "name": "wikivoyage-fixture",
        "version": "1.0.0",
        "description": "fixture",
        "domain": "personal",
        "license": "CC-BY-SA-3.0",
        "provenance": {"source": "https://dumps.wikimedia.org/enwikivoyage/latest/"},
        "build": {"adapter": "wiki_dump", "config": build_config},
    })


# ── XML iter ──────────────────────────────────────────────────────────

def _xml_dump(pages: list[dict]) -> str:
    """Build a Wikimedia-style ``pages-articles.xml`` body."""
    parts = ['<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.10/">']
    for p in pages:
        ns = p.get("namespace", 0)
        parts.append("<page>")
        parts.append(f"<title>{p['title']}</title>")
        parts.append(f"<ns>{ns}</ns>")
        if p.get("redirect"):
            parts.append('<redirect title="Other"/>')
        parts.append("<revision>")
        parts.append(f"<text>{p['text']}</text>")
        parts.append("</revision>")
        parts.append("</page>")
    parts.append("</mediawiki>")
    return "".join(parts)


def test_iter_pages_yields_each_page_with_namespace():
    xml = _xml_dump([
        {"title": "Vienna", "namespace": 0, "text": "==Vienna==\nCapital of Austria. " * 10},
        {"title": "Talk:Vienna", "namespace": 1, "text": "talk page" * 10},
    ])
    stream = BytesIO(xml.encode("utf-8"))
    pages = list(iter_pages_from_dump(stream))
    assert [p.title for p in pages] == ["Vienna", "Talk:Vienna"]
    assert [p.namespace for p in pages] == [0, 1]
    assert pages[0].is_redirect is False


def test_iter_pages_marks_redirect():
    xml = _xml_dump([
        {"title": "Wien", "namespace": 0, "text": "redirected", "redirect": True},
        {"title": "Vienna", "namespace": 0, "text": "real content. " * 30},
    ])
    pages = list(iter_pages_from_dump(BytesIO(xml.encode("utf-8"))))
    assert pages[0].is_redirect is True
    assert pages[1].is_redirect is False


# ── Config validation ─────────────────────────────────────────────────

def test_config_requires_https_dump_url():
    with pytest.raises(PackError, match="must be https"):
        WikiDumpConfig.from_build(BuildSpec(
            adapter="wiki_dump",
            config={"dump_url": "http://insecure/dump.xml.bz2"},
        ))


def test_config_requires_xml_bz2_suffix():
    with pytest.raises(PackError, match="must end with .xml.bz2"):
        WikiDumpConfig.from_build(BuildSpec(
            adapter="wiki_dump",
            config={"dump_url": "https://x/y.zip"},
        ))


def test_config_validates_namespaces_are_ints():
    with pytest.raises(PackError, match="must be ints"):
        WikiDumpConfig.from_build(BuildSpec(
            adapter="wiki_dump",
            config={
                "dump_url": "https://x/y.xml.bz2",
                "include_namespaces": ["main", "talk"],
            },
        ))


def test_config_rejects_empty_namespaces():
    with pytest.raises(PackError, match="non-empty"):
        WikiDumpConfig.from_build(BuildSpec(
            adapter="wiki_dump",
            config={
                "dump_url": "https://x/y.xml.bz2",
                "include_namespaces": [],
            },
        ))


def test_config_rejects_short_sha256():
    with pytest.raises(PackError, match="64-char hex"):
        WikiDumpConfig.from_build(BuildSpec(
            adapter="wiki_dump",
            config={
                "dump_url": "https://x/y.xml.bz2",
                "dump_sha256": "tooshort",
            },
        ))


# ── Adapter behaviour ─────────────────────────────────────────────────

def _stub_downloader(xml_bytes: bytes):
    def _dl(url, dest, ua, max_bytes):
        Path(dest).write_bytes(xml_bytes)
    return _dl


def _plain_opener(path: Path):
    return open(path, "rb")


def test_adapter_writes_each_main_namespace_page(tmp_path):
    xml = _xml_dump([
        {"title": "Vienna", "text": "==Vienna==\nCapital of Austria. " * 30},
        {"title": "Salzburg", "text": "==Salzburg==\nCity in Austria. " * 30},
        {"title": "Talk:Vienna", "namespace": 1, "text": "talk page " * 30},
    ]).encode("utf-8")
    adapter = WikiDumpAdapter(
        downloader=_stub_downloader(xml),
        stream_opener=_plain_opener,
        sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "dump_url": "https://dumps.wikimedia.org/enwikivoyage/latest/x.xml.bz2",
        "min_text_chars": 100,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    names = sorted(p.name for p in result.files)
    # Talk page filtered out by namespace (default include = [0]).
    assert names == ["salzburg.md", "vienna.md"]
    body = (result.content_root / "vienna.md").read_text()
    assert body.startswith("# Vienna")
    assert "## Vienna" in body  # wikitext header converted


def test_adapter_skips_redirects_by_default(tmp_path):
    xml = _xml_dump([
        {"title": "Wien", "text": "redirect", "redirect": True},
        {"title": "Vienna", "text": "Real article body. " * 30},
    ]).encode("utf-8")
    adapter = WikiDumpAdapter(
        downloader=_stub_downloader(xml),
        stream_opener=_plain_opener,
        sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "dump_url": "https://dumps.wikimedia.org/x.xml.bz2",
        "min_text_chars": 100,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert [p.name for p in result.files] == ["vienna.md"]


def test_adapter_includes_redirects_when_disabled(tmp_path):
    xml = _xml_dump([
        {"title": "Wien", "text": "Long enough redirect body. " * 30, "redirect": True},
        {"title": "Vienna", "text": "Real article body. " * 30},
    ]).encode("utf-8")
    adapter = WikiDumpAdapter(
        downloader=_stub_downloader(xml),
        stream_opener=_plain_opener,
        sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "dump_url": "https://dumps.wikimedia.org/x.xml.bz2",
        "min_text_chars": 100,
        "skip_redirects": False,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert sorted(p.name for p in result.files) == ["vienna.md", "wien.md"]


def test_adapter_max_pages_caps_output(tmp_path):
    xml = _xml_dump([
        {"title": f"Page {i}", "text": f"Body {i}. " * 30}
        for i in range(20)
    ]).encode("utf-8")
    adapter = WikiDumpAdapter(
        downloader=_stub_downloader(xml),
        stream_opener=_plain_opener,
        sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "dump_url": "https://dumps.wikimedia.org/x.xml.bz2",
        "min_text_chars": 100,
        "max_pages": 5,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert len(result.files) == 5


def test_adapter_skips_below_min_text(tmp_path):
    xml = _xml_dump([
        {"title": "Stub", "text": "tiny"},
        {"title": "Real", "text": "body of real length. " * 30},
    ]).encode("utf-8")
    adapter = WikiDumpAdapter(
        downloader=_stub_downloader(xml),
        stream_opener=_plain_opener,
        sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "dump_url": "https://dumps.wikimedia.org/x.xml.bz2",
        "min_text_chars": 200,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert [p.name for p in result.files] == ["real.md"]


def test_adapter_includes_alternate_namespaces_via_config(tmp_path):
    xml = _xml_dump([
        {"title": "Help:Editing", "namespace": 12, "text": "help body. " * 30},
        {"title": "Vienna", "namespace": 0, "text": "main body. " * 30},
    ]).encode("utf-8")
    adapter = WikiDumpAdapter(
        downloader=_stub_downloader(xml),
        stream_opener=_plain_opener,
        sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "dump_url": "https://dumps.wikimedia.org/x.xml.bz2",
        "include_namespaces": [0, 12],
        "min_text_chars": 100,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert len(result.files) == 2


def test_adapter_sha256_mismatch_raises(tmp_path):
    xml = _xml_dump([
        {"title": "Vienna", "text": "body. " * 30},
    ]).encode("utf-8")
    adapter = WikiDumpAdapter(
        downloader=_stub_downloader(xml),
        stream_opener=_plain_opener,
        sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "dump_url": "https://dumps.wikimedia.org/x.xml.bz2",
        "dump_sha256": "0" * 64,
        "min_text_chars": 100,
    })
    with pytest.raises(PackError, match="sha256 mismatch"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_adapter_raises_when_no_pages_survive(tmp_path):
    xml = _xml_dump([
        {"title": "Stub", "text": "x"},
    ]).encode("utf-8")
    adapter = WikiDumpAdapter(
        downloader=_stub_downloader(xml),
        stream_opener=_plain_opener,
        sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "dump_url": "https://dumps.wikimedia.org/x.xml.bz2",
        "min_text_chars": 1000,
    })
    with pytest.raises(PackError, match="zero pages survived filter"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_adapter_path_traversal_in_title_blocked(tmp_path):
    xml = _xml_dump([
        {"title": "../escape", "text": "body. " * 30},
    ]).encode("utf-8")
    adapter = WikiDumpAdapter(
        downloader=_stub_downloader(xml),
        stream_opener=_plain_opener,
        sleep=lambda _x: None,
    )
    manifest = _make_manifest(build_config={
        "dump_url": "https://dumps.wikimedia.org/x.xml.bz2",
        "min_text_chars": 100,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert result.files[0].name == "escape.md"
    assert (result.content_root / result.files[0]).is_file()


# ── Registry integration ──────────────────────────────────────────────

def test_wiki_dump_registered():
    assert "wiki_dump" in list_registered_adapters()
    assert isinstance(get_adapter("wiki_dump"), WikiDumpAdapter)


def test_fetch_for_manifest_routes_wiki_dump(tmp_path):
    xml = _xml_dump([
        {"title": "Routed", "text": "body. " * 30},
    ]).encode("utf-8")
    register_adapter(WikiDumpAdapter(
        downloader=_stub_downloader(xml),
        stream_opener=_plain_opener,
        sleep=lambda _x: None,
    ))
    try:
        manifest = _make_manifest(build_config={
            "dump_url": "https://dumps.wikimedia.org/x.xml.bz2",
            "min_text_chars": 100,
        })
        result = fetch_for_manifest(manifest, staging_root=tmp_path)
        assert result.files[0].name == "routed.md"
    finally:
        register_adapter(WikiDumpAdapter())
