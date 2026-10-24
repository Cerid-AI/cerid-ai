# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""A dump of sources, claims, citations, and deletions rebuilds an index."""
from __future__ import annotations

from core.records.corpus import Citation, Claim, Deletion, Source, rebuild


def _source(artifact_id: str = "a1", domain: str = "anneal_lessons") -> Source:
    return Source(
        artifact_id=artifact_id,
        domain=domain,
        sub_category="lesson",
        tags=("lesson", "correction"),
        source_kind="note",
        text="the gate failed closed",
    )


def test_live_records_round_trip_with_their_ontology_fields() -> None:
    source = _source()
    claim = Claim("c1", "a1", "anneal_lessons", "the gate failed closed")
    citation = Citation("c1", "a1")
    index = rebuild([source], [claim], [citation], [])
    assert index["sources"]["a1"].sub_category == "lesson"
    assert index["sources"]["a1"].tags == ("lesson", "correction")
    assert index["claims"]["c1"].text == "the gate failed closed"
    assert index["citations"] == [citation]
    assert index["deleted"] == set()


def test_a_deletion_removes_the_source_its_claims_and_its_citations() -> None:
    index = rebuild(
        [_source(), _source("a2", "mail")],
        [
            Claim("c1", "a1", "anneal_lessons", "gone"),
            Claim("c2", "a2", "mail", "stays"),
        ],
        [Citation("c1", "a1"), Citation("c2", "a2")],
        [Deletion("a1", "forgotten")],
    )
    assert "a1" not in index["sources"]
    assert "c1" not in index["claims"]
    assert index["citations"] == [Citation("c2", "a2")]
    assert index["sources"]["a2"].domain == "mail"
    assert index["deleted"] == {"a1"}


def test_a_claim_whose_domain_disagrees_with_its_source_is_dropped() -> None:
    index = rebuild(
        [_source()],
        [Claim("c1", "a1", "personal", "mislabeled")],
        [Citation("c1", "a1")],
        [],
    )
    assert index["sources"]["a1"].domain == "anneal_lessons"
    assert index["claims"] == {}
    assert index["citations"] == []
