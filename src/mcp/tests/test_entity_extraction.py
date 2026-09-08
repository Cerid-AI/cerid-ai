# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the GraphRAG entity-extraction module.

Covers canonical_id normalisation, schema-validated parsing,
type-vocab filtering, dedup, and the empty/oversize path.
The Neo4j write path is integration-tested separately via the
preservation harness; here we only exercise the pure logic.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from core.agents.entity_extraction import (
    Entity,
    canonical_id,
    extract_entities_from_text,
    is_codec_alias_shaped,
    is_junk_entity,
    is_junk_entity_name,
    is_junk_quantity_name,
    is_shouty_acronym_shaped,
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
        result = await extract_entities_from_text(
            "Elon Musk was asked about Apple Inc. at the briefing.",
            llm_caller=caller,
        )
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
        result = await extract_entities_from_text(
            "Foo and Bar both shipped today.", llm_caller=caller,
        )
        assert [e.canonical_id for e in result] == ["org:bar"]

    async def test_dedup_by_canonical_id(self):
        caller = _llm_caller_returning({
            "entities": [
                {"name": "Elon Musk", "type": "PERSON", "confidence": 0.9},
                {"name": "elon musk", "type": "PERSON", "confidence": 0.7},
                {"name": "ELON MUSK", "type": "PERSON", "confidence": 0.5},
            ]
        })
        result = await extract_entities_from_text(
            "Elon Musk spoke. Elon Musk repeated it.", llm_caller=caller,
        )
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
        result = await extract_entities_from_text(
            "Xx merged with Yy.", llm_caller=caller, min_confidence=0.0,
        )
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
            "Apple Inc. and Some Junk were both listed.",
            llm_caller=caller, min_confidence=0.5,
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
        result_low = await extract_entities_from_text(
            "Below Floor filed a report.", llm_caller=caller_low,
        )
        assert result_low == [], "entity below default floor must be dropped"

        caller_high = _llm_caller_returning({
            "entities": [
                {"name": "Above Floor", "type": "ORG", "confidence": ENTITY_MIN_CONFIDENCE},
            ]
        })
        result_high = await extract_entities_from_text(
            "Above Floor filed a report.", llm_caller=caller_high,
        )
        assert len(result_high) == 1, "entity at or above default floor must survive"


# ---------------------------------------------------------------------------
# _normalise_entities — missing vs. malformed confidence (2026-09-07)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUnreportedConfidence:
    async def test_missing_confidence_kept_at_min_confidence(self):
        """A model that omits ``confidence`` entirely is not the same as 0.0."""
        caller = _llm_caller_returning({
            "entities": [{"name": "Quiet Vendor", "type": "ORG"}],
        })
        result = await extract_entities_from_text(
            "Quiet Vendor shipped the update.", llm_caller=caller, min_confidence=0.5,
        )
        assert len(result) == 1
        assert result[0].confidence == 0.5

    async def test_low_reported_confidence_still_dropped(self):
        """A reported confidence below the threshold is dropped as before."""
        caller = _llm_caller_returning({
            "entities": [{"name": "Low Conf Org", "type": "ORG", "confidence": 0.2}],
        })
        result = await extract_entities_from_text(
            "Low Conf Org filed a report.", llm_caller=caller, min_confidence=0.5,
        )
        assert result == []

    async def test_present_non_numeric_confidence_treated_as_zero_and_dropped(self):
        """A present but unparseable confidence is malformed, not omitted — still 0.0."""
        caller = _llm_caller_returning({
            "entities": [{"name": "Bad Value Org", "type": "ORG", "confidence": "high"}],
        })
        result = await extract_entities_from_text(
            "Bad Value Org filed a report.", llm_caller=caller, min_confidence=0.5,
        )
        assert result == []

    async def test_unreported_confidence_logs_info_once(self, caplog):
        caller = _llm_caller_returning({
            "entities": [
                {"name": "Quiet Vendor", "type": "ORG"},
                {"name": "Loud Vendor", "type": "ORG", "confidence": 0.9},
            ],
        })
        with caplog.at_level("INFO", logger="ai-companion.entity_extraction"):
            await extract_entities_from_text(
                "Quiet Vendor and Loud Vendor both filed reports.",
                llm_caller=caller, min_confidence=0.5,
            )
        info_lines = [
            r for r in caplog.records
            if r.getMessage().startswith("entity_extraction.confidence_unreported")
        ]
        assert len(info_lines) == 1
        assert info_lines[0].getMessage() == (
            "entity_extraction.confidence_unreported n=1 of 2 (kept at threshold 0.50)"
        )

    async def test_all_below_threshold_logs_warning(self, caplog):
        caller = _llm_caller_returning({
            "entities": [{"name": "Low Conf Org", "type": "ORG", "confidence": 0.2}],
        })
        with caplog.at_level("WARNING", logger="ai-companion.entity_extraction"):
            await extract_entities_from_text(
                "Low Conf Org filed a report.", llm_caller=caller, min_confidence=0.5,
            )
        warning_lines = [
            r for r in caplog.records
            if r.getMessage().startswith("entity_extraction.all_below_threshold")
        ]
        assert len(warning_lines) == 1
        assert warning_lines[0].getMessage() == (
            "entity_extraction.all_below_threshold n=1 threshold=0.50"
        )


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

    @pytest.mark.parametrize("name", [
        "a@b",           # the observed live junk entity
        "x@localhost",   # dotless domain — not a real address
        "a@",            # empty domain
    ])
    def test_rejects_degenerate_email_fragments(self, name):
        assert is_junk_entity_name(name) is True

    @pytest.mark.parametrize("name", [
        "john@example.com",   # a real, dotted-domain address
        "@ceridai",           # social handle, not address-shaped
        "R2@D2 Labs",         # contains whitespace — not address-shaped
    ])
    def test_admits_real_addresses_and_handles(self, name):
        assert is_junk_entity_name(name) is False

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
        result = await extract_entities_from_text(
            "See library/email.charset.html for version-3-6 / v3.6.1 notes; "
            "x, NASA and gpt-4 are referenced.",
            llm_caller=caller,
        )
        assert [e.name for e in result] == ["NASA", "gpt-4"]


# ---------------------------------------------------------------------------
# is_junk_quantity_name — type-aware quantity/date gate (2026-09-07)
# ---------------------------------------------------------------------------
# Strings below are pulled verbatim from .extract-eval-readonly/results.json,
# the qwen2.5-3b-vs-7b eval that measured a 4x junk rate on the small model.


class TestJunkQuantityGate:
    """Bare quantities, leaked markdown headings, and content-free DATEs."""

    # -- rejects: bare quantities (observed junk, various types) -------------

    @pytest.mark.parametrize(("name", "entity_type"), [
        ("900 seconds", "DATE"),
        ("15 minutes", "OTHER"),
        ("2 minutes", "OTHER"),
        ("5 grams", "OTHER"),
        ("75°C", "OTHER"),
        ("3 minutes", "DATE"),
        ("42 tokens per second", "OTHER"),
        ("120 tokens", "OTHER"),
        ("10 percent", "OTHER"),
        ("50ms", "OTHER"),
        ("120 to 200 requests", "OTHER"),
        ("250 milliseconds", "OTHER"),
        ("5 retries", "OTHER"),
        ("25 Mbps", "OTHER"),
        ("3 weeks", "ORG"),
        ("5 g", "OTHER"),
        ("22 grams of coffee", "OTHER"),
        ("360 grams of water", "OTHER"),
        ("96 degrees Celsius", "OTHER"),
        ("40-gram bloom", "OTHER"),
    ])
    def test_rejects_bare_quantities(self, name, entity_type):
        assert is_junk_quantity_name(name, entity_type) is True

    # -- rejects: leaked markdown heading markers -----------------------------

    @pytest.mark.parametrize("name", [
        "# Authentication — Token Lifetimes",
        "# Aurora In-Memory Cache — Eviction Policy",
        "# Zephyr API Gateway — Rate Limiter",
        "# Zephyr Client",
        "# Green Tea — Steeping Notes",
        "# Iceland Trip — Packing List",
        "# Project Nimbus — Ledger Datastore Decision",
        "# Project Orion — Budget",
        "# Project Orion",
        "# Team Cadence — Daily Standup",
        "# Project Vega — Launch Timeline",
    ])
    def test_rejects_markdown_heading_fragments(self, name):
        assert is_junk_quantity_name(name, "OTHER") is True

    def test_rejects_punctuation_only_name(self):
        assert is_junk_quantity_name("---", "OTHER") is True

    # -- rejects: DATE-typed names with no date content -----------------------

    @pytest.mark.parametrize(("name", "entity_type"), [
        ("5 retries", "DATE"),
        ("250 milliseconds", "DATE"),
        ("9:30am", "DATE"),
        ("25 Mbps", "DATE"),
    ])
    def test_rejects_non_date_content_typed_as_date(self, name, entity_type):
        assert is_junk_quantity_name(name, entity_type) is True

    # -- admits: real DATE entities -------------------------------------------

    @pytest.mark.parametrize("name", ["March 2026", "2025-11-03", "next Friday"])
    def test_admits_real_dates(self, name):
        assert is_junk_quantity_name(name, "DATE") is False

    # -- admits: digit-bearing real entities ----------------------------------

    @pytest.mark.parametrize(("name", "entity_type"), [
        ("Qwen2.5-7B", "ASSET"),
        ("5G", "OTHER"),
        ("5S", "ASSET"),
        ("3X", "OTHER"),
        ("10.7.0.0/24 subnet", "OTHER"),
        ("Windows 11", "ASSET"),
        ("Boeing 747", "ASSET"),
        ("VLAN 20", "OTHER"),
        ("HTTP 429", "OTHER"),
        ("$85,000", "ASSET"),
        ("100ml bottle", "ASSET"),
        ("24 Hours of Le Mans", "EVENT"),
    ])
    def test_admits_real_digit_bearing_entities(self, name, entity_type):
        assert is_junk_quantity_name(name, entity_type) is False

    # -- end-to-end through the extraction pipeline ---------------------------

    @pytest.mark.asyncio
    async def test_extraction_drops_quantity_junk_keeps_valid(self):
        caller = _llm_caller_returning({
            "entities": [
                {"name": "# Project Orion — Budget", "type": "OTHER", "confidence": 0.9},
                {"name": "900 seconds", "type": "DATE", "confidence": 0.9},
                {"name": "Qwen2.5-7B", "type": "ASSET", "confidence": 0.9},
            ]
        })
        result = await extract_entities_from_text(
            "# Project Orion — Budget\nThe cache TTL is 900 seconds, run on Qwen2.5-7B.",
            llm_caller=caller,
        )
        assert [e.name for e in result] == ["Qwen2.5-7B"]


# ---------------------------------------------------------------------------
# Malformed-JSON retry (2026-09-07)
# ---------------------------------------------------------------------------
# qwen2.5-3b eval: response_format=json_object still produced unquoted enum
# values ("type": DATE) and runaway-repetition truncation on 3/18 fixtures.


@pytest.mark.asyncio
class TestMalformedJsonRetry:
    async def test_retry_succeeds_on_second_valid_reply(self, caplog):
        replies = [
            '{"entities": [{"type": DATE, "name": "bad"}]',  # unquoted enum
            json.dumps({"entities": [{"name": "Zephyr", "type": "ORG", "confidence": 0.9}]}),
        ]
        calls: list[list[dict]] = []

        async def caller(messages):
            calls.append(messages)
            return replies[len(calls) - 1]

        with caplog.at_level("INFO", logger="ai-companion.entity_extraction"):
            result = await extract_entities_from_text(
                "Zephyr shipped the update.", llm_caller=caller,
            )

        assert [e.name for e in result] == ["Zephyr"]
        assert len(calls) == 2
        # Retry call = original messages + one extra user message.
        assert len(calls[1]) == len(calls[0]) + 1
        assert calls[1][-1]["role"] == "user"
        assert "not valid JSON" in calls[1][-1]["content"]

        retry_lines = [
            r.getMessage() for r in caplog.records
            if r.getMessage().startswith("entity_extraction.json_retry")
        ]
        assert retry_lines == ["entity_extraction.json_retry attempted=True succeeded=True"]

    async def test_both_malformed_returns_empty_and_warns(self, caplog):
        async def caller(messages):  # noqa: ARG001
            return "not json at all { incomplete"

        with caplog.at_level("DEBUG", logger="ai-companion.entity_extraction"):
            result = await extract_entities_from_text(
                "test", llm_caller=caller,
            )

        assert result == []
        messages = [r.getMessage() for r in caplog.records]
        assert "entity_extraction.json_retry attempted=True succeeded=False" in messages
        assert any(m.startswith("entity_extraction.json_parse_failed") for m in messages)


# ---------------------------------------------------------------------------
# Example-row personal names — sample data is not a person (todo item 6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExampleRowPersonGate:
    """'John' from SQL VALUES examples passes every other check — the name IS
    in the text — so the gate is contextual: drop a PERSON whose every
    occurrence sits inside SQL example rows, keep the same name in prose."""

    _PERSON_JOHN = {"entities": [{"name": "John", "type": "PERSON", "confidence": 0.9}]}

    async def test_sql_values_only_person_is_dropped(self):
        text = (
            "The tutorial covers inserts.\n"
            "INSERT INTO users (name, age) VALUES ('John', 25);\n"
        )
        result = await extract_entities_from_text(
            text, llm_caller=_llm_caller_returning(self._PERSON_JOHN),
        )
        assert result == []

    async def test_multiline_values_tuple_rows_are_dropped(self):
        text = (
            "Bulk insert example:\n"
            "INSERT INTO users (name, age)\n"
            "VALUES\n"
            "  ('John', 25),\n"
            "  ('Jane', 30);\n"
        )
        result = await extract_entities_from_text(
            text, llm_caller=_llm_caller_returning(self._PERSON_JOHN),
        )
        assert result == []

    async def test_conversational_person_is_kept(self):
        text = "John said he'll review the migration plan on Tuesday. " * 3
        result = await extract_entities_from_text(
            text, llm_caller=_llm_caller_returning(self._PERSON_JOHN),
        )
        assert [e.name for e in result] == ["John"]

    async def test_prose_mention_outweighs_sql_mention(self):
        text = (
            "John wrote this migration for the users table.\n"
            "INSERT INTO users (name) VALUES ('John');\n"
        )
        result = await extract_entities_from_text(
            text, llm_caller=_llm_caller_returning(self._PERSON_JOHN),
        )
        assert [e.name for e in result] == ["John"]

    async def test_org_in_values_is_not_gated(self):
        payload = {"entities": [{"name": "NASA", "type": "ORG", "confidence": 0.9}]}
        text = "INSERT INTO orgs (name) VALUES ('NASA');"
        result = await extract_entities_from_text(
            text, llm_caller=_llm_caller_returning(payload),
        )
        assert [e.name for e in result] == ["NASA"]


# ---------------------------------------------------------------------------
# Prompt-example bleed — the extractor returning its own illustrations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExtractedNamesMustAppearInTheText:
    """The extractor was emitting the prompt's own examples as findings.

    The type list read ``PERSON: real individuals (e.g., "Elon Musk", "Tim
    Cook")`` and so on for every type, and the model copied those illustrations
    into its output as though it had found them. Reproduced live 2026-08-03 on
    a Python asyncio document naming none of them: BTC, Apple Inc., Tim Cook,
    Elon Musk, Tesla Model 3, GPT-4, WWDC, San Francisco, Wall Street and the
    Federal Reserve all came back at confidence 0.9-1.0 — a 1:1 match with the
    example set, alongside the one real entity.

    It was silent and cumulative: each fabrication became a graph node with
    MENTIONS edges to documents that never named it (BTC reached mention_count
    117, Wall Street 132), and the wiki compiler then wrote pages about them —
    which is why summaries read "Apple Inc. is not mentioned in the provided
    excerpts". The excerpts genuinely didn't mention it.
    """

    async def test_the_prompt_carries_no_named_examples(self):
        """Removing the bait is half the fix; this is the half that can rot."""
        from core.agents.entity_extraction import _EXTRACTION_PROMPT

        for bait in ("Elon Musk", "Tim Cook", "Apple Inc.", "BTC", "GPT-4",
                     "Tesla Model 3", "WWDC", "San Francisco", "Wall Street",
                     "Federal Reserve", "Q3 2024"):
            assert bait not in _EXTRACTION_PROMPT, (
                f"{bait!r} is back in the extraction prompt — the model copies "
                "these into its output as extracted entities"
            )

    async def test_the_live_hallucination_is_dropped(self):
        """Replays the exact payload the model returned for runners.md."""
        caller = _llm_caller_returning({"entities": [
            {"name": "asyncio", "type": "ORG", "confidence": 1.0},
            {"name": "BTC", "type": "ASSET", "confidence": 1.0},
            {"name": "Apple Inc.", "type": "ORG", "confidence": 1.0},
            {"name": "Tim Cook", "type": "PERSON", "confidence": 0.9},
            {"name": "Elon Musk", "type": "PERSON", "confidence": 0.9},
            {"name": "Tesla Model 3", "type": "ASSET", "confidence": 0.9},
            {"name": "GPT-4", "type": "ASSET", "confidence": 1.0},
            {"name": "San Francisco", "type": "LOC", "confidence": 0.9},
            {"name": "Wall Street", "type": "LOC", "confidence": 0.9},
            {"name": "Federal Reserve", "type": "ORG", "confidence": 0.9},
        ]})
        # The real opening of the artifact that triggered this.
        text = (
            "Runners\n\nSource code:Lib/asyncio/runners.py\n"
            "This section outlines high-level asyncio primitives to run "
            "asyncio code. They are built on top of an event loop."
        )
        result = await extract_entities_from_text(text, llm_caller=caller)
        assert [e.canonical_id for e in result] == ["org:asyncio"], (
            "only the entity actually named in the text may survive"
        )

    async def test_an_alias_shorter_than_the_extracted_name_survives(self):
        """The filter must not cost real entities.

        A document that writes "Apple" while the extractor returns the fuller
        "Apple Inc." is the common case, and dropping it would trade one silent
        failure for another.
        """
        caller = _llm_caller_returning({"entities": [
            {"name": "Apple Inc.", "type": "ORG", "confidence": 0.95},
        ]})
        result = await extract_entities_from_text(
            "Apple reported record services revenue this quarter.",
            llm_caller=caller,
        )
        assert [e.canonical_id for e in result] == ["org:apple"]

    async def test_matching_is_case_insensitive(self):
        caller = _llm_caller_returning({"entities": [
            {"name": "NASA", "type": "ORG", "confidence": 0.95},
        ]})
        result = await extract_entities_from_text(
            "The nasa budget request was published.", llm_caller=caller,
        )
        assert [e.canonical_id for e in result] == ["org:nasa"]

    async def test_a_name_split_by_formatting_survives(self):
        """The near-miss that would have deleted real data.

        A first version tested the raw name against the raw text. "Matt
        Butcher" written across a line break, or as "**Matt Butcher**", failed
        that test — and the corpus audit built on the same logic listed the
        Helm creator and Azure Kubernetes Service as fabrications. The
        fabrications share no tokens at all with their documents, so widening
        the match separates them without losing genuine entities.
        """
        caller = _llm_caller_returning({"entities": [
            {"name": "Matt Butcher", "type": "PERSON", "confidence": 0.9},
            {"name": "Azure Kubernetes Service", "type": "ORG", "confidence": 0.9},
            {"name": "Tim Cook", "type": "PERSON", "confidence": 0.9},
        ]})
        text = (
            "Charts are maintained by **Matt\nButcher** and others.\n"
            "| Provider | Azure Kubernetes | Service tier |\n"
        )
        result = await extract_entities_from_text(text, llm_caller=caller)
        ids = [e.canonical_id for e in result]
        assert "person:matt-butcher" in ids, "line-broken emphasis must not delete a real person"
        assert "org:azure-kubernetes-service" in ids, "table-split name must survive"
        assert "person:tim-cook" not in ids, "a name sharing no tokens with the text is fabricated"


# ---------------------------------------------------------------------------
# is_junk_entity — combined predicate shared with
# opsrun/purge_junk_entities.py's classify_junk_entity (Task 3, 2026-09-06)
# ---------------------------------------------------------------------------


class TestShoutyAcronymShaped:
    """Moved from app.services.external_apis.wiki_enrichment so the purge
    script and the wiki-refresh pre-enqueue filter share one definition."""

    @pytest.mark.parametrize("name", ["ALIASES", "CHARSETS", "ENCODINGS"])
    def test_rejects_long_shouty_tokens(self, name):
        assert is_shouty_acronym_shaped(name) is True

    @pytest.mark.parametrize("name", ["NASA", "IBM", "UNESCO", "COVID-19", "UTF-8"])
    def test_admits_plausible_acronyms_and_hyphenated_tokens(self, name):
        assert is_shouty_acronym_shaped(name) is False


class TestCodecAliasShaped:
    @pytest.mark.parametrize("name", ["euc-jp", "iso-2022-jp", "utf-8"])
    def test_rejects_codec_alias_shapes(self, name):
        assert is_codec_alias_shaped(name) is True

    @pytest.mark.parametrize("name", ["gpt-4", "scikit-learn"])
    def test_admits_non_codec_hyphenated_tokens(self, name):
        assert is_codec_alias_shaped(name) is False


class TestIsJunkEntity:
    """The predicate app.processor.subscribers.wiki_refresh.enqueue_refresh
    now applies before enqueue — is_junk_entity_name plus the shouty-acronym
    / codec-alias family, gated on the caller's own unknown-type inference so
    core never has to import wiki_enrichment.infer_entity_type."""

    def test_junk_name_gate_alone_is_sufficient(self):
        assert is_junk_entity("library/email.charset.html") is True

    def test_shouty_acronym_only_junk_when_type_unknown(self):
        assert is_junk_entity("ALIASES", entity_type_unknown=True) is True
        assert is_junk_entity("ALIASES", entity_type_unknown=False) is False

    def test_codec_alias_only_junk_when_type_unknown(self):
        assert is_junk_entity("euc-jp", entity_type_unknown=True) is True
        assert is_junk_entity("euc-jp", entity_type_unknown=False) is False

    def test_real_entity_is_never_junk(self):
        assert is_junk_entity("Elon Musk", entity_type_unknown=True) is False
        assert is_junk_entity("NASA", entity_type_unknown=True) is False


# ---------------------------------------------------------------------------
# Recall-eval annotation fixtures (2026-09-07)
# ---------------------------------------------------------------------------
# Non-live sanity check on tests/eval/fixtures/entities/*.json: the live
# recall gate (tests/eval/test_entity_extraction_recall.py) only runs opt-in
# against a real gateway, but a typo'd or stale "expected" name would silently
# tank recall on every run without ever being caught. Whitespace is
# normalised before comparing because the fixture markdown hard-wraps at
# ~70 columns and can break a multi-word expected name across a line.

_EVAL_FIXTURES_DIR = pathlib.Path(__file__).parent / "eval" / "fixtures"
_EVAL_ANNOTATION_FILES = sorted((_EVAL_FIXTURES_DIR / "entities").glob("*.json"))
_MIN_ANNOTATED_FIXTURES = 8
_MIN_FORBIDDEN = 0
_MAX_FORBIDDEN = 4


def _normalise_whitespace(text: str) -> str:
    return " ".join(text.split())


class TestRecallAnnotationFixtures:
    def test_at_least_eight_fixtures_are_annotated(self):
        assert len(_EVAL_ANNOTATION_FILES) >= _MIN_ANNOTATED_FIXTURES

    @pytest.mark.parametrize("annotation_path", _EVAL_ANNOTATION_FILES, ids=lambda p: p.stem)
    def test_expected_names_appear_verbatim_in_fixture_text(self, annotation_path):
        spec = json.loads(annotation_path.read_text())
        fixture_path = _EVAL_FIXTURES_DIR / spec["fixture"]
        assert fixture_path.exists(), f"{spec['fixture']} does not exist"
        text = _normalise_whitespace(fixture_path.read_text()).lower()
        for name in spec["expected"]:
            assert _normalise_whitespace(name).lower() in text, (
                f"{name!r} not found verbatim in {spec['fixture']}"
            )

    @pytest.mark.parametrize("annotation_path", _EVAL_ANNOTATION_FILES, ids=lambda p: p.stem)
    def test_forbidden_list_is_two_to_four_items(self, annotation_path):
        spec = json.loads(annotation_path.read_text())
        assert _MIN_FORBIDDEN <= len(spec["forbidden"]) <= _MAX_FORBIDDEN
