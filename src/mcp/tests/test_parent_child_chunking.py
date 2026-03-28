# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for parent-child document chunking."""

from __future__ import annotations

import pytest

from utils.chunker import (
    chunk_text,
    chunk_with_parents,
    count_tokens,
    get_parent_chunks,
)


def _make_long_text(target_tokens: int) -> str:
    """Generate text that is approximately *target_tokens* tokens long."""
    # "word " is ~1 token per word with cl100k_base; repeat to hit target.
    word = "knowledge "
    text = word * target_tokens
    # Trim to be close to target
    while count_tokens(text) > target_tokens + 10:
        text = text[: text.rfind(" ", 0, -1)]
    return text.strip()


# ── Helpers ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _enable_parent_child(monkeypatch: pytest.MonkeyPatch):
    """Enable the parent-child feature flag for all tests by default."""
    monkeypatch.setenv("ENABLE_PARENT_CHILD_RETRIEVAL", "true")
    # Force re-evaluation of the module-level flag
    import utils.chunker as mod

    monkeypatch.setattr(mod, "PARENT_CHILD_ENABLED", True)


# ── Tests ────────────────────────────────────────────────────────────────


class TestParentChildCreatesBothLevels:
    """Verify both parent and child chunks are created."""

    def test_parent_child_creates_both_levels(self):
        text = _make_long_text(600)  # exceeds 512-token parent limit
        chunks = chunk_with_parents(text, artifact_id="art1", max_tokens=512)

        levels = {c["chunk_level"] for c in chunks}
        assert "parent" in levels
        assert "child" in levels

        parents = [c for c in chunks if c["chunk_level"] == "parent"]
        children = [c for c in chunks if c["chunk_level"] == "child"]
        assert len(parents) >= 1
        assert len(children) >= len(parents)  # each parent has >=1 child


class TestChildHasParentMetadata:
    """Verify metadata fields are present on child chunks."""

    def test_child_has_parent_metadata(self):
        text = _make_long_text(600)
        chunks = chunk_with_parents(text, artifact_id="art2", max_tokens=512)

        children = [c for c in chunks if c["chunk_level"] == "child"]
        assert len(children) > 0

        for child in children:
            assert child["parent_chunk_id"] is not None
            assert child["parent_chunk_id"].startswith("art2_parent_")
            assert child["chunk_level"] == "child"
            assert isinstance(child["child_index"], int)
            assert child["child_index"] >= 0
            assert isinstance(child["parent_token_count"], int)
            assert child["parent_token_count"] > 0


class TestChildCountWithinRatio:
    """Verify child count per parent is within the expected ratio (3-6)."""

    def test_child_count_within_ratio(self):
        # Create text large enough for at least one full-size parent chunk
        text = _make_long_text(1200)
        chunks = chunk_with_parents(text, artifact_id="art3", max_tokens=512)

        parents = [c for c in chunks if c["chunk_level"] == "parent"]
        children = [c for c in chunks if c["chunk_level"] == "child"]

        for parent in parents:
            pid = parent["chunk_id"]
            kids = [c for c in children if c["parent_chunk_id"] == pid]
            parent_tokens = parent["parent_token_count"]

            # Only check ratio bounds for parents with enough tokens to
            # produce multiple children (skip small trailing parents).
            if parent_tokens >= 256:
                assert len(kids) >= 3, (
                    f"Parent {pid} ({parent_tokens} tokens) has only "
                    f"{len(kids)} children — expected >= 3"
                )
                assert len(kids) <= 6, (
                    f"Parent {pid} ({parent_tokens} tokens) has "
                    f"{len(kids)} children — expected <= 6"
                )


class TestParentReconstructsFromChildren:
    """Joining children should approximately reconstruct the parent text."""

    def test_parent_reconstructs_from_children(self):
        text = _make_long_text(600)
        chunks = chunk_with_parents(text, artifact_id="art4", max_tokens=512)

        parents = [c for c in chunks if c["chunk_level"] == "parent"]
        children = [c for c in chunks if c["chunk_level"] == "child"]

        for parent in parents:
            pid = parent["chunk_id"]
            kids = sorted(
                [c for c in children if c["parent_chunk_id"] == pid],
                key=lambda c: c["child_index"],
            )
            if not kids:
                continue

            # The first and last child text should appear in the parent
            assert kids[0]["text"][:50] in parent["text"]
            assert kids[-1]["text"][-50:] in parent["text"]


class TestFeatureFlagDisabledUsesStandard:
    """When the feature flag is off, chunk_with_parents returns flat chunks."""

    def test_feature_flag_disabled_uses_standard(self, monkeypatch):
        import utils.chunker as mod

        monkeypatch.setattr(mod, "PARENT_CHILD_ENABLED", False)

        text = _make_long_text(600)
        chunks = chunk_with_parents(text, artifact_id="art5", max_tokens=512)

        levels = {c["chunk_level"] for c in chunks}
        assert levels == {"flat"}
        # Should match standard chunk_text output count
        standard = chunk_text(text, max_tokens=512)
        assert len(chunks) == len(standard)


class TestShortTextSingleParent:
    """Text shorter than CHUNK_MAX_TOKENS creates 1 parent + children."""

    def test_short_text_single_parent(self):
        text = _make_long_text(200)  # well under 512 limit
        chunks = chunk_with_parents(text, artifact_id="art6", max_tokens=512)

        parents = [c for c in chunks if c["chunk_level"] == "parent"]
        children = [c for c in chunks if c["chunk_level"] == "child"]

        assert len(parents) == 1
        # Short text may produce 1-2 children depending on token count
        assert len(children) >= 1

        # Parent text should equal the original (no splitting needed)
        assert parents[0]["text"].strip() == text.strip()


class TestGetParentChunks:
    """Verify the get_parent_chunks helper."""

    def test_returns_correct_parents(self):
        text = _make_long_text(1200)
        chunks = chunk_with_parents(text, artifact_id="art7", max_tokens=512)

        children = [c for c in chunks if c["chunk_level"] == "child"]
        # Pick children from different parents
        child_ids = [children[0]["chunk_id"]]
        if len(children) > 3:
            child_ids.append(children[-1]["chunk_id"])

        parents = get_parent_chunks(child_ids, chunks)
        assert len(parents) >= 1
        for p in parents:
            assert p["chunk_level"] == "parent"


class TestChunkIdFormats:
    """Verify chunk ID format conventions."""

    def test_parent_id_format(self):
        text = _make_long_text(600)
        chunks = chunk_with_parents(text, artifact_id="doc99", max_tokens=512)

        parents = [c for c in chunks if c["chunk_level"] == "parent"]
        for p in parents:
            assert p["chunk_id"].startswith("doc99_parent_")

    def test_child_id_format(self):
        text = _make_long_text(600)
        chunks = chunk_with_parents(text, artifact_id="doc99", max_tokens=512)

        children = [c for c in chunks if c["chunk_level"] == "child"]
        for c in children:
            assert c["chunk_id"].startswith("doc99_child_")
            # Format: {artifact_id}_child_{parent_idx}_{child_idx}
            parts = c["chunk_id"].split("_")
            assert len(parts) == 4  # doc99, child, parent_idx, child_idx
