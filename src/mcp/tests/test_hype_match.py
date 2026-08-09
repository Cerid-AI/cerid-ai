# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for core.retrieval.hype_match — dedup and dual-query logic."""

from __future__ import annotations

from core.retrieval.hype_match import dedup_with_hype_results

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _content_hit(chunk_id: str, relevance: float, artifact_id: str = "art1") -> dict:
    return {
        "content": f"Content for {chunk_id}",
        "relevance": relevance,
        "chunk_id": chunk_id,
        "artifact_id": artifact_id,
        "filename": "doc.md",
        "domain": "general",
        "chunk_index": 0,
        "collection": "cerid_general",
        "ingested_at": "",
        "sub_category": "",
        "tags_json": "[]",
        "keywords": "[]",
        "memory_type": "",
    }


def _hype_hit(
    hype_doc_id: str,
    source_chunk_id: str,
    relevance: float,
    source_artifact_id: str = "art1",
) -> dict:
    return {
        "content": f"HyPE question for {source_chunk_id}",
        "relevance": relevance,
        "chunk_id": hype_doc_id,
        "source_chunk_id": source_chunk_id,
        "artifact_id": source_artifact_id,
        "filename": "",
        "domain": "",
        "chunk_index": 0,
        "collection": "cerid_general_hype",
        "ingested_at": "",
        "sub_category": "",
        "tags_json": "[]",
        "keywords": "[]",
        "memory_type": "",
        "metadata": {
            "source_chunk_id": source_chunk_id,
            "source_artifact_id": source_artifact_id,
        },
    }


# ---------------------------------------------------------------------------
# dedup_with_hype_results
# ---------------------------------------------------------------------------

class TestDedupWithHypeResults:
    def test_overlap_keeps_higher_relevance_content(self):
        """When HyPE finds a chunk already in content results, higher score wins."""
        content = [_content_hit("c1", 0.7), _content_hit("c2", 0.5)]
        hype = [_hype_hit("c1_hype_0", "c1", 0.9)]  # HyPE score > content score

        merged = dedup_with_hype_results(content, hype)

        c1_entry = next(r for r in merged if r["chunk_id"] == "c1")
        assert c1_entry["relevance"] == 0.9
        assert c1_entry.get("hype_boosted") is True

    def test_overlap_keeps_content_score_when_higher(self):
        """When content score > HyPE score, content score is preserved."""
        content = [_content_hit("c1", 0.9)]
        hype = [_hype_hit("c1_hype_0", "c1", 0.5)]

        merged = dedup_with_hype_results(content, hype)
        c1 = next(r for r in merged if r["chunk_id"] == "c1")
        assert c1["relevance"] == 0.9
        assert not c1.get("hype_boosted")

    def test_hype_only_chunk_is_appended(self):
        """HyPE hit whose parent is NOT in content results is added as new entry."""
        content = [_content_hit("c1", 0.7)]
        hype = [_hype_hit("c2_hype_0", "c2", 0.6)]

        merged = dedup_with_hype_results(content, hype)
        assert len(merged) == 2
        chunk_ids = {r["chunk_id"] for r in merged}
        assert "c1" in chunk_ids
        assert "c2" in chunk_ids

        c2_entry = next(r for r in merged if r["chunk_id"] == "c2")
        assert c2_entry.get("hype_source") is True
        assert c2_entry["relevance"] == 0.6

    def test_empty_hype_returns_content_unchanged(self):
        content = [_content_hit("c1", 0.8), _content_hit("c2", 0.6)]
        merged = dedup_with_hype_results(content, [])
        assert merged == content

    def test_empty_content_and_non_empty_hype(self):
        hype = [_hype_hit("c1_hype_0", "c1", 0.7)]
        merged = dedup_with_hype_results([], hype)
        assert len(merged) == 1
        assert merged[0]["chunk_id"] == "c1"
        assert merged[0].get("hype_source") is True

    def test_result_is_sorted_by_relevance_descending(self):
        content = [_content_hit("c1", 0.5), _content_hit("c2", 0.3)]
        # HyPE boosts c2 to 0.8 and adds a new c3 at 0.9
        hype = [
            _hype_hit("c2_hype_0", "c2", 0.8),
            _hype_hit("c3_hype_0", "c3", 0.9),
        ]
        merged = dedup_with_hype_results(content, hype)
        relevances = [r["relevance"] for r in merged]
        assert relevances == sorted(relevances, reverse=True)

    def test_both_lists_empty_returns_empty(self):
        assert dedup_with_hype_results([], []) == []

    def test_all_hype_match_existing_no_duplicates(self):
        """All HyPE hits resolve to existing chunks — no duplicates in output."""
        content = [_content_hit("c1", 0.6), _content_hit("c2", 0.5)]
        hype = [
            _hype_hit("c1_hype_0", "c1", 0.7),
            _hype_hit("c2_hype_0", "c2", 0.4),
        ]
        merged = dedup_with_hype_results(content, hype)
        assert len(merged) == 2  # exactly original count, no new entries

    def test_hype_hit_without_source_chunk_id_is_skipped(self):
        """HyPE hits with empty source_chunk_id are silently ignored."""
        content = [_content_hit("c1", 0.7)]
        bad_hype = [{
            "content": "orphan question",
            "relevance": 0.9,
            "chunk_id": "orphan_hype_0",
            "source_chunk_id": "",  # empty — cannot resolve parent
            "artifact_id": "art1",
            "filename": "",
            "domain": "",
            "chunk_index": 0,
            "collection": "cerid_general_hype",
            "ingested_at": "",
            "sub_category": "",
            "tags_json": "[]",
            "keywords": "[]",
            "memory_type": "",
            "metadata": {"source_chunk_id": "", "source_artifact_id": "art1"},
        }]
        merged = dedup_with_hype_results(content, bad_hype)
        # Orphan should NOT be appended because source_chunk_id is empty falsy.
        # The function appends when source_chunk_id is truthy-and-not-in-content.
        # Empty string → falls into "no source_chunk_id" branch → skipped.
        assert len(merged) == 1
