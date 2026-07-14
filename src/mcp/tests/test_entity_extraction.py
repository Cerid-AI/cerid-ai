# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the GraphRAG entity-extraction module.

Covers canonical_id normalisation, schema-validated parsing,
type-vocab filtering, dedup, and the empty/oversize path.
The Neo4j write path is integration-tested separately via the
preservation harness; here we only exercise the pure logic.
"""
from __future__ import annotations

import json

import pytest

from core.agents.entity_extraction import (
    Entity,
    canonical_id,
    extract_entities_from_text,
    is_junk_entity_name,
)

# ---------------------------------------------------------------------------
# canonical_id
# ---------------------------------------------------------------------------

class TestCanonicalId:
    def test_basic_person(self):
        assert canonical_id("Elon Musk", "PERSON") == "person:elon-musk"

    def test_org_with_punctuation(self):
        assert canonical_id("Apple Inc.", "ORG") == "org:apple-inc"

    def test_asset_with_slash(self):
        assert canonical_id("BTC/USD", "ASSET") == "asset:btc-usd"

    def test_collapses_whitespace(self):
        assert canonical_id("  Federal   Reserve  ", "ORG") == "org:federal-reserve"

    def test_unicode_folded_to_alnum(self):
        # Non-ASCII collapses to hyphens; the slug is still stable.
        assert canonical_id("Café", "LOC") == "loc:caf"

    def test_case_insensitive(self):
        assert canonical_id("ELON MUSK", "PERSON") == canonical_id("elon musk", "PERSON")


# ---------------------------------------------------------------------------
# extract_entities_from_text — happy path
# ---------------------------------------------------------------------------

def _llm_caller_returning(payload: dict):
    """Build an async caller that always returns the given JSON payload."""
    raw = json.dumps(payload)

    async def caller(messages):  # noqa: ARG001 — messages unused in fake
        return raw

    return caller


@pytest.mark.asyncio
class TestExtractEntities:
    async def test_returns_canonicalised_records(self):
        caller = _llm_caller_returning({
            "entities": [
                {"name": "Elon Musk", "type": "PERSON", "confidence": 0.95},
                {"name": "Apple Inc.", "type": "ORG", "confidence": 0.90},
            ]
        })
        result = await extract_entities_from_text("test", llm_caller=caller)
        assert len(result) == 2
        assert isinstance(result[0], Entity)
        assert result[0].canonical_id == "person:elon-musk"
        # Tier-B normalization strips "Inc." → org:apple (correct post-resolution canonical)
        assert result[1].canonical_id == "org:apple"

    async def test_unknown_type_dropped(self):
        caller = _llm_caller_returning({
            "entities": [
                {"name": "Foo", "type": "GADGET", "confidence": 0.9},
                {"name": "Bar", "type": "ORG", "confidence": 0.9},
            ]
        })
        result = await extract_entities_from_text("test", llm_caller=caller)
        assert [e.canonical_id for e in result] == ["org:bar"]

    async def test_dedup_by_canonical_id(self):
        caller = _llm_caller_returning({
            "entities": [
                {"name": "Elon Musk", "type": "PERSON", "confidence": 0.9},
                {"name": "elon musk", "type": "PERSON", "confidence": 0.7},
                {"name": "ELON MUSK", "type": "PERSON", "confidence": 0.5},
            ]
        })
        result = await extract_entities_from_text("test", llm_caller=caller)
        # First occurrence wins.
        assert len(result) == 1
        assert result[0].confidence == 0.9

    async def test_confidence_clamped_to_unit_interval(self):
        # Two-char names: single characters are rejected by the junk-name gate.
        caller = _llm_caller_returning({
            "entities": [
                {"name": "Xx", "type": "ORG", "confidence": 1.5},
                {"name": "Yy", "type": "ORG", "confidence": -0.3},
            ]
        })
        # min_confidence=0.0 disables the floor so we can test clamping in isolation.
        result = await extract_entities_from_text("test", llm_caller=caller, min_confidence=0.0)
        # Both pass canonicalisation with non-empty slugs.
        confidences = {e.canonical_id: e.confidence for e in result}
        assert confidences["org:xx"] == 1.0
        assert confidences["org:yy"] == 0.0

    async def test_blank_input_returns_empty(self):
        caller = _llm_caller_returning({"entities": []})
        assert await extract_entities_from_text("", llm_caller=caller) == []
        assert await extract_entities_from_text("   ", llm_caller=caller) == []

    async def test_truncates_oversize_text(self):
        captured: list[str] = []

        async def caller(messages):
            captured.append(messages[-1]["content"])
            return json.dumps({"entities": []})

        await extract_entities_from_text(
            "x" * 50_000, llm_caller=caller, max_chars=8000,
        )
        # The prompt template wraps the truncated text; the user message
        # must NOT contain the full 50k characters of "x".
        assert len(captured) == 1
        assert captured[0].count("x") <= 8000 + 100  # +slack for prompt scaffold

    async def test_llm_failure_returns_empty(self):
        async def caller(messages):  # noqa: ARG001
            raise RuntimeError("upstream LLM down")

        assert await extract_entities_from_text("test", llm_caller=caller) == []

    async def test_invalid_json_returns_empty(self):
        async def caller(messages):  # noqa: ARG001
            return "not json at all { incomplete"

        assert await extract_entities_from_text("test", llm_caller=caller) == []

    async def test_non_dict_response_returns_empty(self):
        caller = _llm_caller_returning({"wrong_shape": True})  # type: ignore[arg-type]
        # parse_llm_json returns the dict; missing "entities" key → empty.
        assert await extract_entities_from_text("test", llm_caller=caller) == []

    async def test_confidence_floor_drops_low_confidence_entity(self):
        """Entities below min_confidence are filtered out at extraction time."""
        caller = _llm_caller_returning({
            "entities": [
                {"name": "Apple Inc.", "type": "ORG", "confidence": 0.9},
                {"name": "Some Junk", "type": "ORG", "confidence": 0.3},
            ]
        })
        result = await extract_entities_from_text(
            "test", llm_caller=caller, min_confidence=0.5
        )
        assert len(result) == 1
        # Tier-B normalization strips "Inc." → org:apple (correct post-resolution canonical)
        assert result[0].canonical_id == "org:apple"

    async def test_confidence_floor_default_applied(self):
        """Default threshold (ENTITY_MIN_CONFIDENCE = 0.5) is applied when not overridden."""
        from config.settings import ENTITY_MIN_CONFIDENCE

        caller_low = _llm_caller_returning({
            "entities": [
                {"name": "Below Floor", "type": "ORG", "confidence": ENTITY_MIN_CONFIDENCE - 0.01},
            ]
        })
        result_low = await extract_entities_from_text("test", llm_caller=caller_low)
        assert result_low == [], "entity below default floor must be dropped"

        caller_high = _llm_caller_returning({
            "entities": [
                {"name": "Above Floor", "type": "ORG", "confidence": ENTITY_MIN_CONFIDENCE},
            ]
        })
        result_high = await extract_entities_from_text("test", llm_caller=caller_high)
        assert len(result_high) == 1, "entity at or above default floor must survive"


# ---------------------------------------------------------------------------
# is_junk_entity_name — junk-name gate (2026-07-13)
# ---------------------------------------------------------------------------


class TestJunkNameGate:
    """Structural shapes that cannot be entities are rejected at extraction."""

    # -- rejects -------------------------------------------------------------

    @pytest.mark.parametrize("name", [
        "library/email.charset.html",
        "docs/guide/index.htm",
        "notes/readme.md",
        "spec/rfc.txt",
        "handbook/chapter1.rst",
        "reports/q3.pdf",
    ])
    def test_rejects_doc_file_paths(self, name):
        assert is_junk_entity_name(name) is True

    @pytest.mark.parametrize("name", [
        "version-3-6",
        "3.6",
        "v3.6.1",
        "1.5.0",
        "version 3.6",
    ])
    def test_rejects_pure_version_strings(self, name):
        assert is_junk_entity_name(name) is True

    @pytest.mark.parametrize("name", ["", "   ", "x", "Q", "§"])
    def test_rejects_empty_and_single_characters(self, name):
        assert is_junk_entity_name(name) is True

    # -- admits --------------------------------------------------------------

    @pytest.mark.parametrize("name", [
        "NASA",
        "IBM",
        "gpt-4",
        "scikit-learn",
        "Node.js",
        "BTC/USD",       # slash but no doc extension
        "2024",          # bare number, no separator — may be a DATE entity
        "V8",            # bare v+digits, no separator — product name
        "V-22 Osprey",   # version-ish prefix but not a pure version token
        "index.html",    # doc extension but no slash — could be a topic
        "Elon Musk",
    ])
    def test_admits_valid_entities(self, name):
        assert is_junk_entity_name(name) is False

    # -- end-to-end through the extraction pipeline ---------------------------

    @pytest.mark.asyncio
    async def test_extraction_drops_junk_keeps_valid(self):
        caller = _llm_caller_returning({
            "entities": [
                {"name": "library/email.charset.html", "type": "OTHER", "confidence": 0.9},
                {"name": "version-3-6", "type": "DATE", "confidence": 0.9},
                {"name": "v3.6.1", "type": "OTHER", "confidence": 0.9},
                {"name": "x", "type": "ORG", "confidence": 0.9},
                {"name": "NASA", "type": "ORG", "confidence": 0.9},
                {"name": "gpt-4", "type": "ASSET", "confidence": 0.9},
            ]
        })
        result = await extract_entities_from_text("test", llm_caller=caller)
        assert [e.name for e in result] == ["NASA", "gpt-4"]
