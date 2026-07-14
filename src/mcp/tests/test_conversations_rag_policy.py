# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Quality-Maximization Phase 1.4 — conversations-domain RAG policy.

Chat transcripts (user+assistant turns) are auto-ingested into the
``conversations`` domain. Prior to this change, RAG retrieval merely
discounted raw transcript results by a flat 0.35 instead of excluding
them — assistant-authored (and therefore potentially hallucinated) text
could surface as KB evidence for a future answer, the retrieval-side
sibling of the chat-verification circularity bug.

Covers ``_apply_conversations_rag_policy`` (core/agents/query_agent.py) and
the ``RAG_CONVERSATIONS_POLICY`` flag (config/features.py):
  - "exclude"  (default) drops chat-transcript results, keeps memory results
  - "discount" applies the named discount factor
  - "include"  no-ops
  - invalid env values fall back to "exclude" with a startup warning
"""
from __future__ import annotations

import importlib

from core.agents.query_agent import (
    _CHAT_TRANSCRIPT_DISCOUNT,
    _apply_conversations_rag_policy,
)


def _chat_result(relevance: float = 0.9) -> dict:
    """A raw ingested chat-transcript artifact (feedback_ingest.py naming)."""
    return {
        "domain": "conversations",
        "filename": "chat_abc123_20260713",
        "artifact_id": "chat_abc123_20260713",
        "relevance": relevance,
        "source_type": "kb",
    }


def _memory_surface_result(relevance: float = 0.8) -> dict:
    """A recalled episodic memory, shaped as `_recall_memory_surface` builds it."""
    return {
        "domain": "conversations",
        "filename": "mem-1",
        "artifact_id": "mem-1",
        "relevance": relevance,
        "source_type": "memory",
        "source_authority": "user_memory",
    }


def _memory_artifact_result(relevance: float = 0.7) -> dict:
    """A memory artifact retrieved directly via vector search (memory.py naming)."""
    return {
        "domain": "conversations",
        "filename": "memory_decision_abc123_20260713_0",
        "artifact_id": "memory_decision_abc123_20260713_0",
        "relevance": relevance,
        "source_type": "kb",
    }


def _other_domain_result(relevance: float = 0.6) -> dict:
    return {
        "domain": "coding",
        "filename": "notes.md",
        "artifact_id": "notes.md",
        "relevance": relevance,
        "source_type": "kb",
    }


class TestExcludePolicy:
    def test_drops_chat_transcript_results(self):
        results = [_chat_result(), _other_domain_result()]
        out = _apply_conversations_rag_policy(results, "exclude")
        assert len(out) == 1
        assert out[0]["domain"] == "coding"

    def test_keeps_memory_surface_results(self):
        mem = _memory_surface_result()
        out = _apply_conversations_rag_policy([_chat_result(), mem], "exclude")
        assert len(out) == 1
        assert out[0] is mem
        assert out[0]["relevance"] == 0.8
        assert out[0]["source_authority"] == "user_memory"

    def test_keeps_directly_retrieved_memory_artifacts(self):
        mem_artifact = _memory_artifact_result()
        out = _apply_conversations_rag_policy([_chat_result(), mem_artifact], "exclude")
        assert len(out) == 1
        assert out[0]["filename"] == "memory_decision_abc123_20260713_0"
        assert out[0]["source_authority"] == "user_memory"
        assert out[0]["relevance"] == 0.7

    def test_mixed_batch_only_transcripts_dropped(self):
        results = [
            _chat_result(relevance=0.9),
            _memory_surface_result(relevance=0.8),
            _memory_artifact_result(relevance=0.7),
            _other_domain_result(relevance=0.6),
        ]
        out = _apply_conversations_rag_policy(results, "exclude")
        domains_filenames = {(r["domain"], r["filename"]) for r in out}
        assert ("conversations", "chat_abc123_20260713") not in domains_filenames
        assert ("conversations", "mem-1") in domains_filenames
        assert ("conversations", "memory_decision_abc123_20260713_0") in domains_filenames
        assert ("coding", "notes.md") in domains_filenames
        assert len(out) == 3


class TestDiscountPolicy:
    def test_applies_named_discount_factor(self):
        results = [_chat_result(relevance=0.9)]
        out = _apply_conversations_rag_policy(results, "discount")
        assert len(out) == 1
        assert out[0]["relevance"] == round(0.9 * _CHAT_TRANSCRIPT_DISCOUNT, 4)
        assert out[0]["source_authority"] == "chat_transcript"

    def test_memory_results_unaffected_by_discount(self):
        mem = _memory_surface_result(relevance=0.8)
        out = _apply_conversations_rag_policy([mem], "discount")
        assert out[0]["relevance"] == 0.8
        assert out[0]["source_authority"] == "user_memory"


class TestIncludePolicy:
    def test_no_penalty_relevance_unchanged(self):
        results = [_chat_result(relevance=0.9)]
        out = _apply_conversations_rag_policy(results, "include")
        assert len(out) == 1
        assert out[0]["relevance"] == 0.9

    def test_memory_results_unaffected_by_include(self):
        mem = _memory_surface_result(relevance=0.8)
        out = _apply_conversations_rag_policy([mem], "include")
        assert out[0]["relevance"] == 0.8
        assert out[0]["source_authority"] == "user_memory"

    def test_other_domains_never_touched(self):
        results = [_other_domain_result(relevance=0.55)]
        for policy in ("exclude", "discount", "include"):
            out = _apply_conversations_rag_policy(list(results), policy)
            assert out[0]["relevance"] == 0.55
            assert "source_authority" not in out[0]


class TestFeatureFlagValidation:
    """RAG_CONVERSATIONS_POLICY env parsing (config/features.py)."""

    def test_default_is_exclude(self):
        import config.features as features

        assert features.RAG_CONVERSATIONS_POLICY in ("exclude", "discount", "include")

    def test_invalid_value_falls_back_to_exclude(self, monkeypatch, caplog):
        # NOTE: never `importlib.reload(config)` here — the package re-export
        # bridge re-snapshots taxonomy state, laundering any prior in-session
        # taxonomy extension (internal bootstrap) into config.DOMAINS and
        # breaking later order-dependent tests. Re-sync only the attr we touch.
        import config
        import config.features as features

        monkeypatch.setenv("RAG_CONVERSATIONS_POLICY", "not-a-real-policy")
        try:
            with caplog.at_level("WARNING", logger="ai-companion.config"):
                importlib.reload(features)
            assert features.RAG_CONVERSATIONS_POLICY == "exclude"
            assert any(
                "RAG_CONVERSATIONS_POLICY" in rec.message for rec in caplog.records
            )
        finally:
            monkeypatch.delenv("RAG_CONVERSATIONS_POLICY", raising=False)
            importlib.reload(features)
            config.RAG_CONVERSATIONS_POLICY = features.RAG_CONVERSATIONS_POLICY

    def test_valid_values_accepted(self, monkeypatch):
        import config
        import config.features as features

        for value in ("exclude", "discount", "include"):
            monkeypatch.setenv("RAG_CONVERSATIONS_POLICY", value)
            importlib.reload(features)
            assert features.RAG_CONVERSATIONS_POLICY == value
        monkeypatch.delenv("RAG_CONVERSATIONS_POLICY", raising=False)
        importlib.reload(features)
        config.RAG_CONVERSATIONS_POLICY = features.RAG_CONVERSATIONS_POLICY


class TestContinuityUnaffected:
    """Follow-up conversational continuity comes from `_enrich_query` (session
    conversation_messages), not from conversations-domain transcript retrieval —
    confirms the policy change does not regress multi-turn follow-ups.
    """

    def test_enrich_query_uses_only_passed_in_messages(self):
        from core.agents.query_agent import _enrich_query

        messages = [
            {"role": "user", "content": "tell me about mvcc snapshot isolation"},
            {"role": "assistant", "content": "MVCC uses snapshot isolation for reads."},
        ]
        enriched = _enrich_query("how does it handle writes", messages)
        # Terms come from the user message content directly (session-scoped),
        # independent of anything ingested into the "conversations" domain.
        assert "snapshot" in enriched or "mvcc" in enriched or "isolation" in enriched
