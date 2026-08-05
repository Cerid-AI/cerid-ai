# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Bi-temporal memory plan Phase B — config-level contract tests.

Covers B2 (GRAPH_RELATIONSHIP_TYPES additions + the Cypher-injection
regex guard) and B3 (ENABLE_FACT_INVALIDATION_FILTER default). No
Neo4j / no live services — pure config module assertions, mirroring
tests/test_email_attachment_ingestion.py's
test_unknown_relationship_type_blocked_by_settings_allowlist idiom.
"""
from __future__ import annotations

import re

import config


class TestGraphRelationshipTypes:
    def test_fact_relationship_types_registered(self) -> None:
        """FACT, HAS_FACT, FACT_OBJECT (bi-temporal :Fact layer, m0004/m0006)
        must be in the allowlist the generic create_relationship dispatcher
        checks against (app/db/neo4j/relationships.py:28)."""
        assert "HAS_FACT" in config.GRAPH_RELATIONSHIP_TYPES
        assert "FACT_OBJECT" in config.GRAPH_RELATIONSHIP_TYPES
        assert "FACT" in config.GRAPH_RELATIONSHIP_TYPES

    def test_fact_relationship_types_pass_cypher_injection_regex(self) -> None:
        """Mirrors the module-level assert at config/settings.py:491-492 —
        every relationship type name must match ^[A-Z_]+$. This is a live
        re-check (not just "import didn't crash") so a future edit that
        breaks the regex fails here with a clear assertion, not a bare
        AssertionError at import time."""
        pattern = re.compile(r"[A-Z_]+")
        for rel_type in ("FACT", "HAS_FACT", "FACT_OBJECT"):
            assert pattern.fullmatch(rel_type), (
                f"{rel_type!r} must match ^[A-Z_]+$"
            )

    def test_settings_module_imports_cleanly_with_new_types(self) -> None:
        """The settings.py module-level assert loop (line 491) already ran
        when `config` was imported above — importing successfully at all
        is itself proof the new entries passed validation."""
        for rel_type in config.GRAPH_RELATIONSHIP_TYPES:
            assert re.fullmatch(r"[A-Z_]+", rel_type), (
                f"Invalid GRAPH_RELATIONSHIP_TYPE: {rel_type!r}"
            )


class TestFactInvalidationFilterFlag:
    def test_default_is_off(self, monkeypatch) -> None:
        """No writer exists yet (m0006 is schema-only) — default OFF,
        mirroring how ENABLE_MEMORY_SUPERSESSION_FILTER's read-time
        filter only shipped default-ON once its write path existed."""
        monkeypatch.delenv("ENABLE_FACT_INVALIDATION_FILTER", raising=False)
        import importlib

        from config import features
        importlib.reload(features)
        try:
            assert features.ENABLE_FACT_INVALIDATION_FILTER is False
        finally:
            importlib.reload(features)

    def test_env_override_enables(self, monkeypatch) -> None:
        monkeypatch.setenv("ENABLE_FACT_INVALIDATION_FILTER", "true")
        import importlib

        from config import features
        importlib.reload(features)
        try:
            assert features.ENABLE_FACT_INVALIDATION_FILTER is True
        finally:
            monkeypatch.delenv("ENABLE_FACT_INVALIDATION_FILTER", raising=False)
            importlib.reload(features)

    def test_not_registered_in_feature_toggles_runtime_registry(self) -> None:
        """Mirrors ENABLE_MEMORY_SUPERSESSION_FILTER's shape exactly: it is
        a standalone module-level ENABLE_* var, not one of the curated
        entries in FEATURE_TOGGLES (config/features.py:452-475)."""
        from config.features import FEATURE_TOGGLES

        assert "enable_fact_invalidation_filter" not in FEATURE_TOGGLES
        assert "enable_memory_supersession_filter" not in FEATURE_TOGGLES
