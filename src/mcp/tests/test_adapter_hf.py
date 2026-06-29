# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``core.knowledge.adapter_hf`` — HfDatasetAdapter.

The adapter accepts a DI loader so tests pass a list of dicts instead
of touching the network or the heavyweight ``datasets`` package.
"""
from __future__ import annotations

import pytest

from core.knowledge.adapter_hf import (
    HfDatasetAdapter,
    HfDatasetConfig,
    _slugify,
)
from core.knowledge.adapters import (
    fetch_for_manifest,
    get_adapter,
    list_registered_adapters,
)
from core.knowledge.packs import BuildSpec, PackError, PackManifest


def _make_manifest(*, build_config: dict) -> PackManifest:
    return PackManifest.from_dict({
        "id": "wiki-fixture",
        "name": "Wiki fixture",
        "version": "1.0.0",
        "description": "fixture",
        "domain": "general",
        "license": "CC-BY-SA-3.0",
        "provenance": {"source": "https://huggingface.co/datasets/wikimedia/wikipedia"},
        "build": {"adapter": "hf_dataset", "config": build_config},
    })


def _stub_loader(rows):
    def _load(config: HfDatasetConfig):
        return iter(rows)
    return _load


# ── HfDatasetConfig validation ─────────────────────────────────────────

def test_hf_config_requires_owner_slash_name():
    with pytest.raises(PackError, match="owner/name"):
        HfDatasetConfig.from_build(BuildSpec(
            adapter="hf_dataset",
            config={"dataset_id": "no-slash", "text_field": "text"},
        ))


def test_hf_config_rejects_unsafe_dataset_id():
    with pytest.raises(PackError, match="unsafe characters"):
        HfDatasetConfig.from_build(BuildSpec(
            adapter="hf_dataset",
            config={"dataset_id": "wiki/../media", "text_field": "text"},
        ))


def test_hf_config_requires_text_field():
    with pytest.raises(PackError, match="text_field is required"):
        HfDatasetConfig.from_build(BuildSpec(
            adapter="hf_dataset",
            config={"dataset_id": "x/y"},
        ))


def test_hf_config_rejects_zero_max_rows():
    with pytest.raises(PackError, match="max_rows must be > 0"):
        HfDatasetConfig.from_build(BuildSpec(
            adapter="hf_dataset",
            config={"dataset_id": "x/y", "text_field": "text", "max_rows": 0},
        ))


def test_hf_config_requires_filter_value_with_filter_field():
    with pytest.raises(PackError, match="filter_value is required"):
        HfDatasetConfig.from_build(BuildSpec(
            adapter="hf_dataset",
            config={"dataset_id": "x/y", "text_field": "text", "filter_field": "court"},
        ))


def test_hf_config_round_trips_optional_fields():
    cfg = HfDatasetConfig.from_build(BuildSpec(
        adapter="hf_dataset",
        config={
            "dataset_id": "wikimedia/wikipedia",
            "config_name": "20231101.simple",
            "split": "train",
            "text_field": "text",
            "title_field": "title",
            "id_field": "id",
            "min_text_chars": 200,
            "max_rows": 100,
            "markdown_template": "# {title}\n\n{text}\n",
        },
    ))
    assert cfg.dataset_id == "wikimedia/wikipedia"
    assert cfg.config_name == "20231101.simple"
    assert cfg.title_field == "title"
    assert cfg.min_text_chars == 200
    assert cfg.max_rows == 100


# ── slugify ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("title, expected", [
    ("Plain Title", "plain-title"),
    ("Multiple   spaces", "multiple-spaces"),
    ("Path/Traversal/../../escape", "path-traversal-escape"),
    ("../../etc/passwd", "etc-passwd"),
    ("\x00null-byte", "null-byte"),
    ("Über Schöpfer", "ber-sch-pfer"),  # non-ASCII collapsed to dashes
    ("", "untitled"),
    ("---", "untitled"),
    ("a" * 200, "a" * 80),  # capped at 80 chars
])
def test_slugify_drops_unsafe_chars(title, expected):
    assert _slugify(title) == expected


# ── Adapter.fetch happy paths ─────────────────────────────────────────

def test_hf_adapter_writes_one_markdown_per_row(tmp_path):
    rows = [
        {"id": "12", "title": "Algebra basics", "text": "x" * 500},
        {"id": "13", "title": "Geometry intro", "text": "y" * 500},
    ]
    adapter = HfDatasetAdapter(loader=_stub_loader(rows))
    manifest = _make_manifest(build_config={
        "dataset_id": "wikimedia/wikipedia",
        "config_name": "20231101.simple",
        "text_field": "text",
        "title_field": "title",
        "id_field": "id",
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert sorted(p.name for p in result.files) == [
        "algebra-basics-12.md",
        "geometry-intro-13.md",
    ]
    body = (result.content_root / "algebra-basics-12.md").read_text()
    assert body.startswith("# Algebra basics\n\n")
    assert "x" * 500 in body


def test_hf_adapter_skips_rows_below_min_text_chars(tmp_path):
    rows = [
        {"title": "Short stub", "text": "tiny"},
        {"title": "Real article", "text": "z" * 500},
    ]
    adapter = HfDatasetAdapter(loader=_stub_loader(rows))
    manifest = _make_manifest(build_config={
        "dataset_id": "wikimedia/wikipedia",
        "text_field": "text", "title_field": "title", "min_text_chars": 200,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert len(result.files) == 1
    assert result.files[0].name == "real-article.md"


def test_hf_adapter_max_rows_caps_output(tmp_path):
    rows = [
        {"title": f"Article {i}", "text": "z" * 500} for i in range(50)
    ]
    adapter = HfDatasetAdapter(loader=_stub_loader(rows))
    manifest = _make_manifest(build_config={
        "dataset_id": "wikimedia/wikipedia",
        "text_field": "text", "title_field": "title", "max_rows": 5,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert len(result.files) == 5


def test_hf_adapter_filters_by_field(tmp_path):
    """filter_field/filter_value keep only matching rows (court-scoped subset)."""
    rows = [
        {"title": "SCOTUS case", "text": "z" * 500, "court": "U.S. Supreme Court"},
        {"title": "Appeals case", "text": "z" * 500, "court": "9th Circuit"},
        {"title": "Another SCOTUS", "text": "z" * 500, "court": "U.S. Supreme Court"},
    ]
    adapter = HfDatasetAdapter(loader=_stub_loader(rows))
    manifest = _make_manifest(build_config={
        "dataset_id": "free-law/Caselaw_Access_Project", "text_field": "text",
        "title_field": "title", "filter_field": "court",
        "filter_value": "U.S. Supreme Court",
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    names = sorted(p.name for p in result.files)
    assert names == ["another-scotus.md", "scotus-case.md"]


def test_hf_adapter_handles_title_collisions_via_counter(tmp_path):
    """Two rows with the same slug + no id_field must not overwrite each other."""
    rows = [
        {"title": "Same Title", "text": "z" * 500},
        {"title": "Same Title", "text": "z" * 500},
        {"title": "Same Title", "text": "z" * 500},
    ]
    adapter = HfDatasetAdapter(loader=_stub_loader(rows))
    manifest = _make_manifest(build_config={
        "dataset_id": "x/y", "text_field": "text", "title_field": "title",
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    names = sorted(p.name for p in result.files)
    assert names == ["same-title-1.md", "same-title-2.md", "same-title.md"]


def test_hf_adapter_synthesises_title_when_field_missing(tmp_path):
    rows = [{"text": "z" * 500}, {"text": "y" * 500}]
    adapter = HfDatasetAdapter(loader=_stub_loader(rows))
    manifest = _make_manifest(build_config={
        "dataset_id": "x/y", "text_field": "text",
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    names = sorted(p.name for p in result.files)
    assert names == ["row-0.md", "row-1.md"]


def test_hf_adapter_raises_when_no_rows_pass_filter(tmp_path):
    rows = [{"text": "tiny"}, {"text": "also tiny"}]
    adapter = HfDatasetAdapter(loader=_stub_loader(rows))
    manifest = _make_manifest(build_config={
        "dataset_id": "x/y", "text_field": "text", "min_text_chars": 200,
    })
    with pytest.raises(PackError, match="zero rows survived filter"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_hf_adapter_skips_non_string_text(tmp_path):
    rows = [
        {"title": "Bad row", "text": 42},  # non-string
        {"title": "Good row", "text": "z" * 500},
    ]
    adapter = HfDatasetAdapter(loader=_stub_loader(rows))
    manifest = _make_manifest(build_config={
        "dataset_id": "x/y", "text_field": "text", "title_field": "title",
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert len(result.files) == 1
    assert result.files[0].name == "good-row.md"


def test_hf_adapter_path_traversal_blocked_at_slug_layer(tmp_path):
    """A row whose title is `../escape` must be slugified down to a safe stem."""
    rows = [{"title": "../escape", "text": "z" * 500}]
    adapter = HfDatasetAdapter(loader=_stub_loader(rows))
    manifest = _make_manifest(build_config={
        "dataset_id": "x/y", "text_field": "text", "title_field": "title",
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert result.files[0].name == "escape.md"
    # File definitely under content_root.
    written = result.content_root / result.files[0]
    assert written.is_file()


# ── Registry integration ──────────────────────────────────────────────

def test_hf_dataset_registered_after_module_import():
    # adapter_hf imports register_adapter at module top, so loading
    # should have already registered it.
    assert "hf_dataset" in list_registered_adapters()
    assert isinstance(get_adapter("hf_dataset"), HfDatasetAdapter)


def test_fetch_for_manifest_routes_hf_dataset(tmp_path):
    """fetch_for_manifest dispatches to the registered hf_dataset adapter.

    The registered instance was constructed with the default loader, so
    we re-register an instance bound to a stub loader for the test
    duration, then restore the default to keep the test isolated.
    """
    from core.knowledge.adapters import register_adapter

    rows = [{"title": "Alpha", "text": "z" * 500}]
    register_adapter(HfDatasetAdapter(loader=_stub_loader(rows)))
    try:
        manifest = _make_manifest(build_config={
            "dataset_id": "x/y", "text_field": "text", "title_field": "title",
        })
        result = fetch_for_manifest(manifest, staging_root=tmp_path)
        assert result.files[0].name == "alpha.md"
    finally:
        register_adapter(HfDatasetAdapter())


def test_default_loader_raises_clear_error_when_datasets_missing(monkeypatch):
    """If the heavy `datasets` library isn't installed, error must point
    operators at the install hint rather than surfacing ImportError raw."""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "datasets":
            raise ImportError("simulated absence")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    from core.knowledge.adapter_hf import _default_dataset_loader

    cfg = HfDatasetConfig.from_build(BuildSpec(
        adapter="hf_dataset",
        config={"dataset_id": "x/y", "text_field": "text"},
    ))
    with pytest.raises(PackError, match="datasets.*not installed"):
        _default_dataset_loader(cfg)
