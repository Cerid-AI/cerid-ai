# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for ``core.ingest.vault_config`` (RAG Cycle C2.3 Phase A)."""
from __future__ import annotations

import os

import pytest

from core.ingest.vault_config import (
    DEFAULT_VAULT_CONFIG,
    VAULT_CONFIG_FILENAME,
    PathClassification,
    VaultProfile,
    build_profile,
    load_vault_yaml,
    profile_to_dict,
)

# ---------------------------------------------------------------------------
# classify_path
# ---------------------------------------------------------------------------

def _vault_profile(**overrides) -> VaultProfile:
    """Build a VaultProfile with defaults, overridable per test."""
    defaults = dict(
        root_path="/vault",
        mocs_folders=("mocs",),
        daily_folders=("daily",),
        templates_folders=("templates",),
        attachments_folders=("attachments",),
        skip_folders=(".obsidian", ".trash"),
        default_domain="general",
    )
    defaults.update(overrides)
    return VaultProfile(**defaults)


@pytest.mark.parametrize("rel,expected", [
    ("mocs/index.md", PathClassification.MOC),
    ("mocs/sub/nested.md", PathClassification.MOC),
    ("daily/2026-05-01.md", PathClassification.DAILY),
    ("templates/note.md", PathClassification.SKIP),
    (".obsidian/workspace.json", PathClassification.SKIP),
    (".trash/old.md", PathClassification.SKIP),
    ("attachments/diagram.pdf", PathClassification.ATTACHMENT),
    ("notes/idea.md", PathClassification.REGULAR),
    ("root_file.md", PathClassification.REGULAR),
])
def test_classify_path_basic(rel, expected):
    assert _vault_profile().classify_path(rel) is expected


def test_classify_path_case_insensitive():
    # Profile carries lower-cased names, but classification matches
    # regardless of how the on-disk folder is cased.
    profile = _vault_profile()
    assert profile.classify_path("MOCs/index.md") is PathClassification.MOC
    assert profile.classify_path("DAILY/today.md") is PathClassification.DAILY
    assert profile.classify_path("Templates/x.md") is PathClassification.SKIP


def test_classify_path_handles_windows_separators():
    profile = _vault_profile()
    assert profile.classify_path("mocs\\index.md") is PathClassification.MOC


def test_classify_path_empty_returns_regular():
    assert _vault_profile().classify_path("") is PathClassification.REGULAR
    assert _vault_profile().classify_path("./") is PathClassification.REGULAR
    assert _vault_profile().classify_path("/") is PathClassification.REGULAR


def test_classify_path_skip_wins_over_template():
    # If a folder is listed under both skip_folders and templates_folders,
    # SKIP still wins (templates also returns SKIP, so the outcome is the
    # same — this test guards the docstring contract).
    profile = _vault_profile(
        templates_folders=("templates",),
        skip_folders=("templates", ".obsidian"),
    )
    assert profile.classify_path("templates/x.md") is PathClassification.SKIP


def test_classify_path_unconfigured_category_falls_through():
    # If mocs_folders is empty, "mocs/" is just a regular folder.
    profile = _vault_profile(mocs_folders=())
    assert profile.classify_path("mocs/index.md") is PathClassification.REGULAR


# ---------------------------------------------------------------------------
# load_vault_yaml
# ---------------------------------------------------------------------------

def test_load_vault_yaml_missing_file(tmp_path):
    assert load_vault_yaml(str(tmp_path)) is None


def test_load_vault_yaml_empty_root_returns_none():
    assert load_vault_yaml("") is None


def test_load_vault_yaml_parses_valid_file(tmp_path):
    yaml_text = "mocs_folders:\n  - my-mocs\ndefault_domain: research\n"
    (tmp_path / VAULT_CONFIG_FILENAME).write_text(yaml_text, encoding="utf-8")
    data = load_vault_yaml(str(tmp_path))
    assert data == {"mocs_folders": ["my-mocs"], "default_domain": "research"}


def test_load_vault_yaml_malformed_returns_empty_dict(tmp_path):
    # A genuinely malformed YAML scalar+mapping mix.
    (tmp_path / VAULT_CONFIG_FILENAME).write_text(":\n  - [unclosed", encoding="utf-8")
    data = load_vault_yaml(str(tmp_path))
    assert data == {}


def test_load_vault_yaml_empty_file_returns_empty_dict(tmp_path):
    (tmp_path / VAULT_CONFIG_FILENAME).write_text("", encoding="utf-8")
    # ``yaml.safe_load("")`` returns None — coerced to {} by our loader.
    data = load_vault_yaml(str(tmp_path))
    assert data == {}


def test_load_vault_yaml_top_level_list_returns_empty(tmp_path):
    # A YAML list at the top level is meaningless — treat as empty.
    (tmp_path / VAULT_CONFIG_FILENAME).write_text("- foo\n- bar\n", encoding="utf-8")
    assert load_vault_yaml(str(tmp_path)) == {}


# ---------------------------------------------------------------------------
# build_profile
# ---------------------------------------------------------------------------

def test_build_profile_defaults_only(tmp_path):
    profile = build_profile(str(tmp_path), None)
    assert "mocs" in profile.mocs_folders
    assert "daily" in profile.daily_folders
    assert "templates" in profile.templates_folders
    assert profile.default_domain == DEFAULT_VAULT_CONFIG["default_domain"]


def test_build_profile_ui_config_overrides_defaults(tmp_path):
    ui = {"mocs_folders": ["custom-mocs"], "default_domain": "research"}
    profile = build_profile(str(tmp_path), ui)
    assert profile.mocs_folders == ("custom-mocs",)
    assert profile.default_domain == "research"
    # Other keys still come from defaults.
    assert "daily" in profile.daily_folders


def test_build_profile_yaml_overrides_ui(tmp_path):
    ui = {"mocs_folders": ["ui-mocs"], "default_domain": "ui"}
    (tmp_path / VAULT_CONFIG_FILENAME).write_text(
        "mocs_folders:\n  - yaml-mocs\ndefault_domain: yaml\n",
        encoding="utf-8",
    )
    profile = build_profile(str(tmp_path), ui)
    assert profile.mocs_folders == ("yaml-mocs",)
    assert profile.default_domain == "yaml"


def test_build_profile_ui_fills_keys_yaml_omits(tmp_path):
    # YAML sets only mocs_folders. UI provides daily_folders. Both
    # should win over defaults for their respective keys.
    ui = {"daily_folders": ["my-journal"]}
    (tmp_path / VAULT_CONFIG_FILENAME).write_text(
        "mocs_folders:\n  - vault-mocs\n",
        encoding="utf-8",
    )
    profile = build_profile(str(tmp_path), ui)
    assert profile.mocs_folders == ("vault-mocs",)
    assert profile.daily_folders == ("my-journal",)
    # templates_folders still defaulted
    assert "templates" in profile.templates_folders


def test_build_profile_normalises_case(tmp_path):
    ui = {"mocs_folders": ["MOCs", "Maps Of Content"]}
    profile = build_profile(str(tmp_path), ui)
    # Lower-cased — classification compares against lower-cased input.
    assert profile.mocs_folders == ("mocs", "maps of content")


def test_build_profile_accepts_string_value(tmp_path):
    # A user writing `mocs_folders: my-mocs` (without list syntax) in
    # YAML — Obsidian's implicit-single-value form.
    (tmp_path / VAULT_CONFIG_FILENAME).write_text(
        "mocs_folders: my-mocs\n",
        encoding="utf-8",
    )
    profile = build_profile(str(tmp_path), None)
    assert profile.mocs_folders == ("my-mocs",)


def test_build_profile_explicit_empty_yaml_list_clears_category(tmp_path):
    (tmp_path / VAULT_CONFIG_FILENAME).write_text(
        "mocs_folders: []\n",
        encoding="utf-8",
    )
    profile = build_profile(str(tmp_path), None)
    # Explicit override to "no mocs folders" — defaults must NOT fill it.
    assert profile.mocs_folders == ()


def test_build_profile_default_domain_blank_falls_back(tmp_path):
    ui = {"default_domain": "   "}
    profile = build_profile(str(tmp_path), ui)
    assert profile.default_domain == DEFAULT_VAULT_CONFIG["default_domain"]


def test_build_profile_drops_unknown_keys(tmp_path):
    # An unknown key in YAML must not crash and must not appear anywhere.
    (tmp_path / VAULT_CONFIG_FILENAME).write_text(
        "evil_key: dangerous\nmocs_folders:\n  - moc\n",
        encoding="utf-8",
    )
    profile = build_profile(str(tmp_path), None)
    assert profile.mocs_folders == ("moc",)
    # No "evil_key" attribute leakage — VaultProfile is a frozen dataclass.
    assert not hasattr(profile, "evil_key")


# ---------------------------------------------------------------------------
# profile_to_dict
# ---------------------------------------------------------------------------

def test_profile_to_dict_shape():
    profile = _vault_profile()
    d = profile_to_dict(profile)
    assert set(d.keys()) == {
        "root_path",
        "mocs_folders",
        "daily_folders",
        "templates_folders",
        "attachments_folders",
        "skip_folders",
        "default_domain",
    }
    # All folder lists are list[str] (not tuples) so the JSON shape is stable.
    assert isinstance(d["mocs_folders"], list)
    assert isinstance(d["skip_folders"], list)


def test_build_profile_handles_missing_root(tmp_path):
    # If the configured vault root doesn't yet exist, build_profile must
    # not raise — the scanner can be misconfigured before it runs.
    missing = os.path.join(str(tmp_path), "does-not-exist")
    profile = build_profile(missing, None)
    assert profile.default_domain == DEFAULT_VAULT_CONFIG["default_domain"]
