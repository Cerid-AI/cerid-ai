# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase C — C1 bi-temporal fact derivation (pure, no store/LLM).

Covers the STATE-vs-EVENT fact_key rule, the count(DISTINCT) EVENT fixture (the
"how many yoga classes" shape), provenance-source propagation, valid-time
resolution + fallback, the per-memory cap, and unary-fact shape.
"""
from __future__ import annotations

from core.agents.fact_derivation import (
    FACT_SOURCE_EXTRACTION,
    FACT_SOURCE_VERIFICATION,
    MAX_FACTS_PER_MEMORY,
    build_fact_key,
    derive_facts,
    fact_uid,
    is_state_memory_type,
    resolve_fact_source,
    resolve_valid_from,
)

_YOGA = "other:yoga-class"


def _uids(facts) -> set[str]:
    return {fact_uid(f.subject_id, f.fact_key) for f in facts}


# ---------------------------------------------------------------------------
# STATE vs EVENT classification + fact_key rule
# ---------------------------------------------------------------------------


def test_state_types_are_power_law():
    assert is_state_memory_type("empirical")
    assert is_state_memory_type("decision")
    assert is_state_memory_type("preference")


def test_event_types_are_not_state():
    assert not is_state_memory_type("temporal")
    assert not is_state_memory_type("project_context")
    assert not is_state_memory_type("conversational")


def test_state_fact_key_excludes_event_date():
    facts = derive_facts(
        content="User lives in Denver.",
        memory_type="empirical",
        event_date="2026-03-01",
        observation_date="2026-03-01",
        entity_ids=["loc:denver"],
    )
    assert len(facts) == 1
    f = facts[0]
    assert f.is_state is True
    # STATE: event_date must NOT appear in the key (newer value replaces).
    assert "2026-03-01" not in f.fact_key
    assert f.fact_key == "empirical"


def test_event_fact_key_includes_event_date():
    facts = derive_facts(
        content="Attended a yoga class.",
        memory_type="conversational",
        event_date="2026-03-01",
        observation_date="2026-03-01",
        entity_ids=[_YOGA],
    )
    assert len(facts) == 1
    f = facts[0]
    assert f.is_state is False
    # EVENT: event_date participates in the key (each occurrence distinct).
    assert f.fact_key == "conversational|2026-03-01"


# ---------------------------------------------------------------------------
# The count(DISTINCT) EVENT fixture — "how many yoga classes"
# ---------------------------------------------------------------------------


def test_count_distinct_event_facts_by_date():
    """N distinct-dated EVENT memories about one subject -> N distinct facts."""
    dates = ["2026-03-01", "2026-03-08", "2026-03-15"]
    all_facts = []
    for d in dates:
        all_facts += derive_facts(
            content="Attended a yoga class.",
            memory_type="conversational",
            event_date=d,
            observation_date=d,
            entity_ids=[_YOGA],
        )
    # Three distinct dates -> three distinct uids -> count(DISTINCT) == 3.
    assert len(_uids(all_facts)) == 3


def test_same_dated_event_dedups_to_one():
    """The same dated occurrence re-extracted collapses to one uid (MERGE)."""
    facts_a = derive_facts(
        content="Attended a yoga class.",
        memory_type="conversational",
        event_date="2026-03-01",
        observation_date="2026-03-01",
        entity_ids=[_YOGA],
    )
    facts_b = derive_facts(
        content="Went to yoga class.",  # different phrasing, same subject+date
        memory_type="conversational",
        event_date="2026-03-01",
        observation_date="2026-03-01",
        entity_ids=[_YOGA],
    )
    assert _uids(facts_a) == _uids(facts_b)


def test_state_same_subject_dedups_across_dates():
    """STATE facts about one subject collapse regardless of event_date."""
    facts_a = derive_facts(
        content="User lives in Denver.",
        memory_type="empirical",
        event_date="2026-01-01",
        observation_date="2026-01-01",
        entity_ids=["loc:denver"],
    )
    facts_b = derive_facts(
        content="User lives in Denver.",
        memory_type="empirical",
        event_date="2026-06-01",
        observation_date="2026-06-01",
        entity_ids=["loc:denver"],
    )
    assert _uids(facts_a) == _uids(facts_b)


# ---------------------------------------------------------------------------
# Provenance source flag (Risk R5)
# ---------------------------------------------------------------------------


def test_verification_source_propagates():
    assert resolve_fact_source("verification") == FACT_SOURCE_VERIFICATION
    facts = derive_facts(
        content="Python's GIL prevents true parallelism.",
        memory_type="empirical",
        event_date=None,
        observation_date="2026-03-01",
        entity_ids=["other:gil"],
        memory_source_type="verification",
    )
    assert facts[0].source == FACT_SOURCE_VERIFICATION


def test_default_source_is_extraction():
    assert resolve_fact_source(None) == FACT_SOURCE_EXTRACTION
    facts = derive_facts(
        content="Python's GIL prevents true parallelism.",
        memory_type="empirical",
        event_date=None,
        observation_date="2026-03-01",
        entity_ids=["other:gil"],
    )
    assert facts[0].source == FACT_SOURCE_EXTRACTION


# ---------------------------------------------------------------------------
# Valid-time resolution + fallback (mirrors bitemporal.py exactly)
# ---------------------------------------------------------------------------


def test_valid_from_prefers_event_date():
    assert resolve_valid_from(event_date="2026-03-01", observation_date="2026-06-01") == "2026-03-01"


def test_valid_from_falls_back_to_observation():
    assert resolve_valid_from(event_date=None, observation_date="2026-06-01") == "2026-06-01"
    assert resolve_valid_from(event_date="", observation_date="2026-06-01") == "2026-06-01"
    assert resolve_valid_from(event_date="null", observation_date="2026-06-01") == "2026-06-01"


def test_valid_from_empty_when_nothing_known():
    assert resolve_valid_from(event_date=None, observation_date=None) == ""


def test_derived_valid_from_matches_resolution():
    facts = derive_facts(
        content="Attended a yoga class.",
        memory_type="conversational",
        event_date="2026-03-01",
        observation_date="2026-02-01",
        entity_ids=[_YOGA],
    )
    assert facts[0].valid_from == "2026-03-01"


# ---------------------------------------------------------------------------
# Cap + shape
# ---------------------------------------------------------------------------


def test_cap_bounds_facts_per_memory():
    many = [f"other:e{i:03d}" for i in range(MAX_FACTS_PER_MEMORY * 2)]
    facts = derive_facts(
        content="lots of entities",
        memory_type="conversational",
        event_date="2026-03-01",
        observation_date="2026-03-01",
        entity_ids=many,
    )
    assert len(facts) == MAX_FACTS_PER_MEMORY
    # Deterministic: the first MAX (sorted) subjects.
    assert sorted(f.subject_id for f in facts) == sorted(many)[:MAX_FACTS_PER_MEMORY]


def test_facts_are_unary():
    facts = derive_facts(
        content="Attended a yoga class.",
        memory_type="conversational",
        event_date="2026-03-01",
        observation_date="2026-03-01",
        entity_ids=[_YOGA],
    )
    assert facts[0].object_id is None


def test_empty_content_or_no_entities_yields_nothing():
    assert derive_facts(
        content="",
        memory_type="conversational",
        event_date="2026-03-01",
        observation_date="2026-03-01",
        entity_ids=[_YOGA],
    ) == []
    assert derive_facts(
        content="something",
        memory_type="conversational",
        event_date="2026-03-01",
        observation_date="2026-03-01",
        entity_ids=[],
    ) == []


# ---------------------------------------------------------------------------
# fact_key / uid helpers
# ---------------------------------------------------------------------------


def test_build_fact_key_binary_state():
    key = build_fact_key("attended", "other:gym", "2026-03-01", is_state=True)
    assert key == "attended|other:gym"


def test_build_fact_key_binary_event():
    key = build_fact_key("attended", "other:gym", "2026-03-01", is_state=False)
    assert key == "attended|other:gym|2026-03-01"


def test_fact_uid_is_subject_pipe_key():
    assert fact_uid("loc:denver", "empirical") == "loc:denver|empirical"
