# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for ``core.knowledge.adapter_python_docs``.

Build a fake docs zip in-memory + drive the adapter via its DI
downloader. No network.
"""
from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from core.knowledge.adapter_python_docs import (
    PythonDocsZipAdapter,
    PythonDocsZipConfig,
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
        "id": "py-stdlib-fixture",
        "name": "py-stdlib-fixture",
        "version": "1.0.0",
        "description": "fixture",
        "domain": "coding",
        "license": "PSF-2.0",
        "provenance": {"source": "https://docs.python.org/3/download.html"},
        "build": {"adapter": "python_docs_zip", "config": build_config},
    })


def _make_docs_zip(
    *,
    top_dir: str = "python-3.13.0-docs-html",
    files: dict[str, str] | None = None,
) -> bytes:
    files = files or {
        "library/os.html": (
            "<html><body><div class='body'><h1>os — OS interfaces</h1>"
            "<p>" + "Documentation for the os module. " * 30 + "</p>"
            "</div></body></html>"
        ),
        "library/sys.html": (
            "<html><body><div class='body'><h1>sys — System parameters</h1>"
            "<p>" + "Documentation for the sys module. " * 30 + "</p>"
            "</div></body></html>"
        ),
        "tutorial/intro.html": (
            "<html><body><div class='body'><h1>Tutorial: intro</h1>"
            "<p>" + "Welcome to Python tutorials. " * 30 + "</p>"
            "</div></body></html>"
        ),
        "library/internal/__init__.html": "<html><body>tiny</body></html>",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, html in files.items():
            zf.writestr(f"{top_dir}/{rel}", html)
    return buf.getvalue()


def _stub_downloader(zip_bytes: bytes):
    def _dl(url, max_bytes):
        return zip_bytes
    return _dl


# ── Config validation ─────────────────────────────────────────────────

def test_config_requires_https_archive_url():
    with pytest.raises(PackError, match="must be https"):
        PythonDocsZipConfig.from_build(BuildSpec(
            adapter="python_docs_zip",
            config={"archive_url": "http://insecure/x.zip"},
        ))


def test_config_requires_zip_extension():
    with pytest.raises(PackError, match="must end in .zip"):
        PythonDocsZipConfig.from_build(BuildSpec(
            adapter="python_docs_zip",
            config={"archive_url": "https://x/y.tar.gz"},
        ))


def test_config_validates_sha256_length():
    with pytest.raises(PackError, match="64-char hex"):
        PythonDocsZipConfig.from_build(BuildSpec(
            adapter="python_docs_zip",
            config={
                "archive_url": "https://x/y.zip",
                "archive_sha256": "tooshort",
            },
        ))


def test_config_default_globs():
    cfg = PythonDocsZipConfig.from_build(BuildSpec(
        adapter="python_docs_zip",
        config={"archive_url": "https://x/y.zip"},
    ))
    assert cfg.include_globs == ("library/**/*.html",)


# ── Adapter behaviour ────────────────────────────────────────────────

def test_adapter_writes_each_html_under_globs(tmp_path):
    zip_bytes = _make_docs_zip()
    adapter = PythonDocsZipAdapter(downloader=_stub_downloader(zip_bytes))
    manifest = _make_manifest(build_config={
        "archive_url": "https://docs.python.org/x.zip",
        "include_globs": ["library/**/*.html"],
        "min_text_chars": 200,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    names = sorted(p.name for p in result.files)
    # Tutorial dropped (not under library/), tiny under-threshold internal also dropped.
    assert names == ["os-os-interfaces.md", "sys-system-parameters.md"]
    body = (result.content_root / "os-os-interfaces.md").read_text()
    assert body.startswith("# os — OS interfaces")
    assert "Documentation for the os module." in body
    assert "source: library/os.html" in body


def test_adapter_max_pages_caps_after_filter(tmp_path):
    files = {
        f"library/mod{i}.html":
            f"<html><body><div class='body'><h1>mod{i}</h1>"
            f"<p>" + f"Doc for mod{i}. " * 30 + "</p></div></body></html>"
        for i in range(20)
    }
    zip_bytes = _make_docs_zip(files=files)
    adapter = PythonDocsZipAdapter(downloader=_stub_downloader(zip_bytes))
    manifest = _make_manifest(build_config={
        "archive_url": "https://docs.python.org/x.zip",
        "include_globs": ["library/**/*.html"],
        "max_pages": 5,
        "min_text_chars": 200,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert len(result.files) == 5


def test_adapter_exclude_globs_filter(tmp_path):
    files = {
        "library/os.html":
            "<html><body><div class='body'><h1>os</h1><p>" + "x" * 500 + "</p></div></body></html>",
        "library/__init__.html":
            "<html><body><div class='body'><h1>private</h1><p>" + "x" * 500 + "</p></div></body></html>",
    }
    zip_bytes = _make_docs_zip(files=files)
    adapter = PythonDocsZipAdapter(downloader=_stub_downloader(zip_bytes))
    manifest = _make_manifest(build_config={
        "archive_url": "https://docs.python.org/x.zip",
        "include_globs": ["library/**/*.html"],
        "exclude_globs": ["library/__init__.html"],
        "min_text_chars": 200,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert [p.name for p in result.files] == ["os.md"]


def test_adapter_sha256_mismatch_raises(tmp_path):
    zip_bytes = _make_docs_zip()
    adapter = PythonDocsZipAdapter(downloader=_stub_downloader(zip_bytes))
    manifest = _make_manifest(build_config={
        "archive_url": "https://docs.python.org/x.zip",
        "archive_sha256": "0" * 64,
        "include_globs": ["library/**/*.html"],
        "min_text_chars": 200,
    })
    with pytest.raises(PackError, match="archive sha256 mismatch"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_adapter_sha256_match_passes(tmp_path):
    zip_bytes = _make_docs_zip()
    digest = hashlib.sha256(zip_bytes).hexdigest()
    adapter = PythonDocsZipAdapter(downloader=_stub_downloader(zip_bytes))
    manifest = _make_manifest(build_config={
        "archive_url": "https://docs.python.org/x.zip",
        "archive_sha256": digest,
        "include_globs": ["library/**/*.html"],
        "min_text_chars": 200,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert len(result.files) == 2


def test_adapter_rejects_multi_top_dir_zip(tmp_path):
    """Defence against a bundle that smuggles two top-level entries."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("dir-a/library/os.html", "<html/>")
        zf.writestr("dir-b/library/os.html", "<html/>")
    bad = buf.getvalue()
    adapter = PythonDocsZipAdapter(downloader=_stub_downloader(bad))
    manifest = _make_manifest(build_config={
        "archive_url": "https://docs.python.org/x.zip",
        "include_globs": ["library/**/*.html"],
    })
    with pytest.raises(PackError, match="exactly one top-level"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_adapter_raises_when_no_files_match(tmp_path):
    zip_bytes = _make_docs_zip()
    adapter = PythonDocsZipAdapter(downloader=_stub_downloader(zip_bytes))
    manifest = _make_manifest(build_config={
        "archive_url": "https://docs.python.org/x.zip",
        "include_globs": ["does-not-exist/**/*.html"],
        "min_text_chars": 200,
    })
    with pytest.raises(PackError, match="zero files survived"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_adapter_uses_filename_stem_when_title_missing(tmp_path):
    files = {
        "library/notitle.html":
            "<html><body><div class='body'><p>"
            + "body without an h1. " * 30 + "</p></div></body></html>",
    }
    zip_bytes = _make_docs_zip(files=files)
    adapter = PythonDocsZipAdapter(downloader=_stub_downloader(zip_bytes))
    manifest = _make_manifest(build_config={
        "archive_url": "https://docs.python.org/x.zip",
        "include_globs": ["library/**/*.html"],
        "min_text_chars": 200,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert result.files[0].name == "notitle.md"


# ── Registry integration ─────────────────────────────────────────────

def test_python_docs_zip_registered():
    assert "python_docs_zip" in list_registered_adapters()
    assert isinstance(get_adapter("python_docs_zip"), PythonDocsZipAdapter)


def test_fetch_for_manifest_routes_python_docs_zip(tmp_path):
    zip_bytes = _make_docs_zip()
    register_adapter(PythonDocsZipAdapter(downloader=_stub_downloader(zip_bytes)))
    try:
        manifest = _make_manifest(build_config={
            "archive_url": "https://docs.python.org/x.zip",
            "include_globs": ["library/**/*.html"],
            "min_text_chars": 200,
        })
        result = fetch_for_manifest(manifest, staging_root=tmp_path)
        assert len(result.files) >= 1
    finally:
        register_adapter(PythonDocsZipAdapter())
