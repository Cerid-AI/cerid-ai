# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Phase C — fact derivation+write wiring inside the entity-extraction job.

Exercises ``_derive_and_write_facts`` directly (the seam the plan names): the
ENABLE_FACT_WRITES gate, the conversations-domain + memory_type restriction,
entity reuse (no extra LLM call), valid-time threading from the stored memory,
provenance-source threading, and best-effort failure isolation.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.processor.jobs.entity_extraction import _derive_and_write_facts
from core.agents.entity_extraction import Entity


def _entities():
    return [
        Entity(name="Yoga", entity_type="OTHER", canonical_id="other:yoga-class", confidence=0.9),
        Entity(name="Denver", entity_type="LOC", canonical_id="loc:denver", confidence=0.8),
    ]


def _meta(**over):
    base = {
        "memory_type": "conversational",
        "event_date": "2026-03-01",
        "valid_from": "2026-03-01",
    }
    base.update(over)
    return base


def _call(*, domain="conversations", metadatas=None, entities=None, flag=True):
    metadatas = metadatas if metadatas is not None else [_meta()]
    entities = entities if entities is not None else _entities()
    with patch("config.features.ENABLE_FACT_WRITES", flag):
        return _derive_and_write_facts(
            MagicMock(),
            artifact_id="art-1",
            tenant_id="t-1",
            domain=domain,
            content="Attended a yoga class in Denver.",
            chunk_metadatas=metadatas,
            entities=entities,
        )


def test_flag_off_skips_fact_write():
    with patch("app.db.neo4j.facts.write_facts") as w:
        result = _call(flag=False)
    assert result == {}
    w.assert_not_called()


def test_wrong_domain_skips_fact_write():
    with patch("app.db.neo4j.facts.write_facts") as w:
        result = _call(domain="general", flag=True)
    assert result == {}
    w.assert_not_called()


def test_missing_memory_type_skips_fact_write():
    # A chat-transcript artifact in the conversations domain carries no
    # memory_type — the fact step must not fire on it.
    with patch("app.db.neo4j.facts.write_facts") as w:
        result = _call(metadatas=[{"filename": "chat_x"}], flag=True)
    assert result == {}
    w.assert_not_called()


def test_flag_on_derives_and_writes():
    with patch(
        "app.db.neo4j.facts.write_facts", return_value={"facts_written": 2, "chunks": 1}
    ) as w:
        result = _call(flag=True)
    assert result == {"facts_written": 2, "chunks": 1}
    w.assert_called_once()
    _, kwargs = w.call_args
    facts = w.call_args.args[1]
    assert kwargs["source_artifact_id"] == "art-1"
    # One fact per resolved entity (reused from THIS job — no extra LLM call).
    assert {f.subject_id for f in facts} == {"other:yoga-class", "loc:denver"}
    # EVENT type -> valid_from seeded from event_date.
    assert all(f.valid_from == "2026-03-01" for f in facts)


def test_valid_from_threaded_from_stored_valid_from():
    # No event_date in metadata: fact valid_from must equal the memory's stored
    # valid_from (so the graph and Chroma stores never diverge).
    with patch(
        "app.db.neo4j.facts.write_facts", return_value={"facts_written": 2}
    ) as w:
        _call(metadatas=[_meta(event_date="", valid_from="2026-05-05")], flag=True)
    facts = w.call_args.args[1]
    assert all(f.valid_from == "2026-05-05" for f in facts)


def test_verification_source_threaded():
    with patch(
        "app.db.neo4j.facts.write_facts", return_value={"facts_written": 2}
    ) as w:
        _call(metadatas=[_meta(memory_source_type="verification")], flag=True)
    facts = w.call_args.args[1]
    assert all(f.source == "verification" for f in facts)


def test_write_failure_is_swallowed():
    with patch(
        "app.db.neo4j.facts.write_facts", side_effect=RuntimeError("neo4j down")
    ):
        with patch(
            "app.processor.jobs.entity_extraction.log_swallowed_error"
        ) as mock_log:
            result = _call(flag=True)
    assert result == {}
    mock_log.assert_called_once()
