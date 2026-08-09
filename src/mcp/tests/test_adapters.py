# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for ``core.knowledge.adapters`` — Strategy ABC + GithubZipAdapter.

Adapter tests use an in-memory fake zip downloader so no network +
no GitHub auth + no shell-out. The fake is constructed via stdlib
``zipfile``, so the test fixtures exercise the same parser path as
production.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from core.knowledge.adapters import (
    GithubZipAdapter,
    GithubZipConfig,
    PackError,
    PackSourceAdapter,
    fetch_for_manifest,
    get_adapter,
    list_registered_adapters,
    register_adapter,
)
from core.knowledge.packs import BuildSpec, PackManifest

# ── Fake-zip fixture ──────────────────────────────────────────────────

def _make_github_codeload_zip(
    *,
    top_dir: str = "rust-lang-book-abc1234",
    files: dict[str, bytes] | None = None,
) -> bytes:
    """Return a bytes-zip mimicking GitHub's codeload archive layout.

    GitHub bundles a repo as ``{repo-name}-{ref-or-sha}/...``. The
    adapter strips that prefix universally so tests should set up files
    with that nesting.
    """
    files = files or {
        "src/intro.md": b"# intro\n",
        "src/chapter-01.md": b"# ch 1\n",
        "src/translations/de.md": b"# de\n",
        "Cargo.toml": b"[package]\n",
        "LICENSE-MIT": b"mit text\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, payload in files.items():
            zf.writestr(f"{top_dir}/{rel}", payload)
    return buf.getvalue()


def _make_manifest(*, build_config: dict) -> PackManifest:
    return PackManifest.from_dict({
        "id": "rust-book",
        "name": "Rust Book",
        "version": "1.0.0",
        "description": "fixture",
        "domain": "coding",
        "license": "MIT",
        "provenance": {"source": "https://github.com/rust-lang/book"},
        "build": {"adapter": "github_zip", "config": build_config},
    })


def _stub_downloader(zip_bytes: bytes):
    """Return a downloader that always returns the given bytes."""
    def _dl(url: str, max_bytes: int) -> bytes:
        return zip_bytes
    return _dl


# ── Adapter ABC + registry ────────────────────────────────────────────

def test_default_adapter_registry_has_github_zip():
    assert "github_zip" in list_registered_adapters()
    assert isinstance(get_adapter("github_zip"), PackSourceAdapter)


def test_get_adapter_unknown_raises():
    with pytest.raises(PackError, match="Unknown adapter"):
        get_adapter("does-not-exist")


def test_register_adapter_requires_name():
    class _Empty(PackSourceAdapter):
        name = ""

        def fetch(self, manifest, *, staging_root):
            return None  # type: ignore[return-value]

    with pytest.raises(PackError, match="non-empty"):
        register_adapter(_Empty())


# ── GithubZipConfig validation ─────────────────────────────────────────

def test_github_zip_config_requires_owner_slash_name():
    with pytest.raises(PackError, match="owner/name"):
        GithubZipConfig.from_build(BuildSpec(
            adapter="github_zip", config={"repo": "no-slash", "include_globs": ["*"]},
        ))


def test_github_zip_config_rejects_traversal_in_repo():
    with pytest.raises(PackError, match="unsafe characters"):
        GithubZipConfig.from_build(BuildSpec(
            adapter="github_zip",
            config={"repo": "rust-lang/../book", "include_globs": ["*"]},
        ))


def test_github_zip_config_requires_include_globs():
    with pytest.raises(PackError, match="include_globs must be non-empty"):
        GithubZipConfig.from_build(BuildSpec(
            adapter="github_zip", config={"repo": "x/y"},
        ))


def test_github_zip_config_archive_url_uses_https():
    cfg = GithubZipConfig.from_build(BuildSpec(
        adapter="github_zip",
        config={"repo": "rust-lang/book", "ref": "v1.85.0", "include_globs": ["*.md"]},
    ))
    assert cfg.archive_url == "https://github.com/rust-lang/book/archive/v1.85.0.zip"


# ── GithubZipAdapter.fetch happy path + filtering ──────────────────────

def test_github_zip_adapter_filters_by_include_globs(tmp_path):
    zip_bytes = _make_github_codeload_zip()
    adapter = GithubZipAdapter(downloader=_stub_downloader(zip_bytes))
    manifest = _make_manifest(build_config={
        "repo": "rust-lang/book",
        "include_globs": ["src/**/*.md"],
        "exclude_globs": ["src/translations/**"],
    })

    result = adapter.fetch(manifest, staging_root=tmp_path)

    kept = sorted(p.as_posix() for p in result.files)
    assert kept == ["src/chapter-01.md", "src/intro.md"]
    # Excluded paths must not appear on disk either.
    assert not (result.content_root / "src" / "translations" / "de.md").exists()
    # Matched files were written.
    assert (result.content_root / "src" / "intro.md").read_bytes() == b"# intro\n"


def test_github_zip_adapter_strip_prefix(tmp_path):
    zip_bytes = _make_github_codeload_zip()
    adapter = GithubZipAdapter(downloader=_stub_downloader(zip_bytes))
    manifest = _make_manifest(build_config={
        "repo": "rust-lang/book",
        "include_globs": ["src/**/*.md"],
        "strip_prefix": "src/",
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    kept = sorted(p.as_posix() for p in result.files)
    # `src/` prefix removed from every kept path.
    assert kept == ["chapter-01.md", "intro.md", "translations/de.md"]


def test_github_zip_adapter_raises_when_no_files_match(tmp_path):
    zip_bytes = _make_github_codeload_zip()
    adapter = GithubZipAdapter(downloader=_stub_downloader(zip_bytes))
    manifest = _make_manifest(build_config={
        "repo": "rust-lang/book",
        "include_globs": ["nonexistent/**/*.md"],
    })
    with pytest.raises(PackError, match="matched zero files"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_github_zip_adapter_rejects_multiple_top_dirs(tmp_path):
    """A zip with two top-level entries is rejected — defence against
    a fork that bundles multiple roots (typo-squatting hardening)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("repo-a/README.md", b"a\n")
        zf.writestr("repo-b/README.md", b"b\n")
    bad = buf.getvalue()
    adapter = GithubZipAdapter(downloader=_stub_downloader(bad))
    manifest = _make_manifest(build_config={
        "repo": "x/y", "include_globs": ["*.md"],
    })
    with pytest.raises(PackError, match="exactly one top-level directory"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_github_zip_adapter_blocks_path_traversal(tmp_path):
    """Even after top-dir stripping, an absolute or `..`-laden member must
    not escape staging_root. (zipfile's own _extract_member normalises
    most cases, but defence in depth.)"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("repo-a/legit.md", b"ok\n")
        zf.writestr("repo-a/../escape.md", b"escape\n")
    bad = buf.getvalue()
    adapter = GithubZipAdapter(downloader=_stub_downloader(bad))
    manifest = _make_manifest(build_config={
        "repo": "x/y", "include_globs": ["**/*.md"],
    })
    # The adapter either rejects the multiple-top-dir result OR rejects
    # the traversal — either is fine. It must NOT silently extract
    # "escape.md" outside content_root.
    with pytest.raises(PackError):
        adapter.fetch(manifest, staging_root=tmp_path)


# ── PackManifest.build round-trip ──────────────────────────────────────

def test_pack_manifest_build_round_trip():
    raw = {
        "id": "x",
        "name": "x",
        "version": "1.0.0",
        "description": "x",
        "domain": "general",
        "license": "MIT",
        "provenance": {"source": "https://github.com/mdn/content"},
        "build": {
            "adapter": "github_zip",
            "config": {"repo": "mdn/content", "include_globs": ["files/**/*.md"]},
        },
    }
    pack = PackManifest.from_dict(raw)
    assert pack.build is not None
    assert pack.build.adapter == "github_zip"
    assert pack.build.config["repo"] == "mdn/content"
    again = PackManifest.from_dict(pack.to_dict())
    assert again.build == pack.build


def test_pack_manifest_to_dict_omits_build_when_none():
    pack = PackManifest.from_dict({
        "id": "x", "name": "x", "version": "1.0.0",
        "description": "x", "domain": "general", "license": "CC0-1.0",
    })
    out = pack.to_dict()
    assert "build" not in out


def test_build_spec_requires_adapter_field():
    with pytest.raises(PackError, match="BuildSpec.adapter is required"):
        BuildSpec.from_dict({"config": {}})


# ── fetch_for_manifest convenience entry point ─────────────────────────

def test_fetch_for_manifest_routes_to_correct_adapter(tmp_path):
    zip_bytes = _make_github_codeload_zip()
    # Replace the registered adapter with one that uses our stub downloader.
    register_adapter(GithubZipAdapter(downloader=_stub_downloader(zip_bytes)))
    try:
        manifest = _make_manifest(build_config={
            "repo": "rust-lang/book",
            "include_globs": ["src/**/*.md"],
        })
        result = fetch_for_manifest(manifest, staging_root=tmp_path)
        assert len(result.files) == 3  # intro + ch1 + translations/de
    finally:
        # Restore the default adapter so subsequent tests aren't affected.
        register_adapter(GithubZipAdapter())


def test_fetch_for_manifest_without_build_raises(tmp_path):
    manifest = PackManifest.from_dict({
        "id": "x", "name": "x", "version": "1.0.0",
        "description": "x", "domain": "general", "license": "CC0-1.0",
        "provenance": {"source": "https://github.com/mdn/content"},
    })
    with pytest.raises(PackError, match="no build spec"):
        fetch_for_manifest(manifest, staging_root=tmp_path)
