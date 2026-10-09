# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
from __future__ import annotations

from core.retrieval.chunk_ids import (
    ChunkIdAssigner,
    chunk_artifact_id,
    chunk_body,
    content_chunk_id,
    is_positional_chunk_id,
)

AID = "a" * 64


def test_body_ignores_header_and_contextual_line():
    plain = "The quarterly revenue rose by four percent."
    headed = f"Source: report.pdf | Domain: finance | Category: tax\n\n{plain}"
    contextual = f"[Q3 revenue in annual report]\n{headed}"
    assert chunk_body(plain) == chunk_body(headed) == chunk_body(contextual) == plain


def test_body_keeps_a_mid_text_source_line():
    text = "Intro line.\n\nSource: not a header\n\nMore."
    assert chunk_body(text) == "Intro line. Source: not a header More."


def test_body_collapses_whitespace_and_normalizes_unicode():
    assert chunk_body("  café \n\t bar  ") == "café bar"


def test_id_is_stable_and_owned_by_the_artifact():
    cid = content_chunk_id(AID, "child", "some text", 0)
    assert cid == content_chunk_id(AID, "child", "some text", 0)
    assert cid.startswith(f"{AID}_")
    assert len(cid) == len(AID) + 1 + 16
    assert not is_positional_chunk_id(cid)


def test_level_occurrence_and_artifact_change_the_id():
    base = content_chunk_id(AID, "child", "t", 0)
    assert content_chunk_id(AID, "parent", "t", 0) != base
    assert content_chunk_id(AID, "child", "t", 1) != base
    assert content_chunk_id("b" * 64, "child", "t", 0).split("_")[1] != base.split("_")[1]


def test_non_parent_levels_hash_as_child():
    assert content_chunk_id(AID, "flat", "t", 0) == content_chunk_id(AID, "child", "t", 0)
    assert content_chunk_id(AID, "", "t", 0) == content_chunk_id(AID, "child", "t", 0)


def test_assigner_counts_repeats_per_level_and_body():
    ids = ChunkIdAssigner(AID)
    first = ids.assign("child", "repeat")
    parent = ids.assign("parent", "repeat")
    second = ids.assign("child", "Source: x.md | Domain: code\n\nrepeat")
    assert first == content_chunk_id(AID, "child", "repeat", 0)
    assert parent == content_chunk_id(AID, "parent", "repeat", 0)
    assert second == content_chunk_id(AID, "child", "repeat", 1)


def test_positional_ids_are_recognised():
    assert is_positional_chunk_id(f"{AID}_chunk_0")
    assert is_positional_chunk_id(f"{AID}_chunk_12")
    assert is_positional_chunk_id(f"{AID}_parent_3")
    assert is_positional_chunk_id(f"{AID}_child_3_7")
    assert not is_positional_chunk_id("verified_memory_abc")
    assert not is_positional_chunk_id(f"{AID}_chunk_0_hype_1")


def test_chunk_artifact_id_reads_both_forms():
    cid = content_chunk_id(AID, "child", "t", 0)
    assert chunk_artifact_id(cid) == AID
    assert chunk_artifact_id(f"{AID}_child_2_3") == AID
    assert chunk_artifact_id("verified_memory_abc") is None
    assert chunk_artifact_id("_0123456789abcdef") is None
