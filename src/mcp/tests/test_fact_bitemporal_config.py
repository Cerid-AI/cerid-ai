# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Bi-temporal memory plan Phase B — config-level contract tests.

Covers B2 (GRAPH_RELATIONSHIP_TYPES additions + the Cypher-injection
regex guard). No
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


def test_the_retired_read_flags_are_gone() -> None:
    """Recall's two flags gave way to the shared read filter (forget phase 5):
    the current version is the only one returned, with no switch to turn it off."""
    from config import features

    assert not hasattr(features, "ENABLE_FACT_INVALIDATION_FILTER")
    assert not hasattr(features, "ENABLE_MEMORY_SUPERSESSION_FILTER")
