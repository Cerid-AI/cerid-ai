# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""CL-7/AF-012: boot rehydration of operator-created :Domain nodes into config."""
from __future__ import annotations

import config
from app.startup.domain_rehydration import merge_persisted_domains


def test_merge_adds_new_runtime_domain(monkeypatch):
    """A persisted domain absent from the static taxonomy is added to BOTH
    config.TAXONOMY and config.DOMAINS — the fix that lets a runtime-added domain
    survive a restart (its live consumers read config.DOMAINS)."""
    monkeypatch.setattr(config, "TAXONOMY", dict(config.TAXONOMY))
    monkeypatch.setattr(config, "DOMAINS", list(config.DOMAINS))

    added = merge_persisted_domains(
        [{"name": "customx", "description": "d", "icon": "i", "sub_categories": ["s1", "s2"]}]
    )

    assert added == 1
    assert "customx" in config.TAXONOMY
    assert "customx" in config.DOMAINS
    assert config.TAXONOMY["customx"]["sub_categories"] == ["s1", "s2"]


def test_merge_is_add_only_never_overwrites_static(monkeypatch):
    """Add-only: a persisted domain already in the static taxonomy is neither
    re-counted nor overwritten (static config is authoritative)."""
    monkeypatch.setattr(config, "TAXONOMY", dict(config.TAXONOMY))
    monkeypatch.setattr(config, "DOMAINS", list(config.DOMAINS))
    existing = next(iter(config.TAXONOMY))
    original = config.TAXONOMY[existing]

    added = merge_persisted_domains(
        [{"name": existing, "description": "OVERWRITE", "icon": "X", "sub_categories": []}]
    )

    assert added == 0
    assert config.TAXONOMY[existing] == original


def test_merge_empty_list_is_noop(monkeypatch):
    monkeypatch.setattr(config, "TAXONOMY", dict(config.TAXONOMY))
    monkeypatch.setattr(config, "DOMAINS", list(config.DOMAINS))
    before = list(config.DOMAINS)
    assert merge_persisted_domains([]) == 0
    assert config.DOMAINS == before
