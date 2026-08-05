# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for MMR diversity reordering module."""

import pytest

from core.utils.diversity import _extract_terms, cosine_similarity, jaccard_similarity, mmr_reorder


class TestExtractTerms:
    """Tests for _extract_terms()."""

    def test_basic_extraction(self):
        terms = _extract_terms("The quick brown fox jumps over the lazy dog")
        assert "quick" in terms
        assert "brown" in terms
        assert "fox" in terms

    def test_stopwords_removed(self):
        terms = _extract_terms("the is a an of in for on with at by")
        assert len(terms) == 0

    def test_short_words_removed(self):
        terms = _extract_terms("is it ok to go do")
        assert len(terms) == 0

    def test_suffix_stripping(self):
        terms = _extract_terms("running jumping processing completed")
        # After stripping -ing/-ed: runn, jump, process, complet
        assert any("jump" in t for t in terms)
        assert any("process" in t for t in terms)

    def test_empty_text(self):
        terms = _extract_terms("")
        assert len(terms) == 0


class TestJaccardSimilarity:
    """Tests for jaccard_similarity()."""

    def test_identical_sets(self):
        s = frozenset({"a", "b", "c"})
        assert jaccard_similarity(s, s) == 1.0

    def test_disjoint_sets(self):
        a = frozenset({"a", "b"})
        b = frozenset({"c", "d"})
        assert jaccard_similarity(a, b) == 0.0

    def test_partial_overlap(self):
        a = frozenset({"a", "b", "c"})
        b = frozenset({"b", "c", "d"})
        assert jaccard_similarity(a, b) == 0.5

    def test_empty_sets(self):
        assert jaccard_similarity(frozenset(), frozenset({"a"})) == 0.0
        assert jaccard_similarity(frozenset({"a"}), frozenset()) == 0.0
        assert jaccard_similarity(frozenset(), frozenset()) == 0.0


class TestMmrReorder:
    """Tests for mmr_reorder()."""

    def test_empty_results(self):
        assert mmr_reorder([], "test query") == []

    def test_single_result(self):
        results = [{"content": "hello world example", "relevance": 0.9}]
        reordered = mmr_reorder(results, "hello")
        assert len(reordered) == 1
        assert reordered[0]["relevance"] == 0.9

    def test_diverse_results_promoted(self):
        results = [
            {"content": "machine learning neural networks deep learning", "relevance": 0.9},
            {"content": "machine learning neural networks deep learning models", "relevance": 0.85},
            {"content": "database optimization query performance indexes", "relevance": 0.8},
        ]
        reordered = mmr_reorder(results, "machine learning and databases")
        contents = [r["content"] for r in reordered]
        db_idx = next(i for i, c in enumerate(contents) if "database" in c)
        dup_idx = next(i for i, c in enumerate(contents) if "models" in c)
        assert db_idx < dup_idx

    def test_lambda_one_pure_relevance(self):
        results = [
            {"content": "alpha beta gamma", "relevance": 0.5},
            {"content": "alpha beta gamma delta", "relevance": 0.9},
            {"content": "epsilon zeta eta", "relevance": 0.7},
        ]
        reordered = mmr_reorder(results, "alpha", lambda_param=1.0)
        assert reordered[0]["relevance"] == 0.9

    def test_top_n_limits_output(self):
        results = [
            {"content": f"document number {i} content text here", "relevance": 0.5 + i * 0.1}
            for i in range(5)
        ]
        reordered = mmr_reorder(results, "document", top_n=3)
        assert len(reordered) == 3

    def test_missing_content_handled(self):
        results = [
            {"relevance": 0.9},
            {"content": "hello world example text", "relevance": 0.8},
        ]
        reordered = mmr_reorder(results, "hello")
        assert len(reordered) == 2

    def test_preserves_all_result_fields(self):
        results = [
            {"content": "alpha content text here", "relevance": 0.9, "artifact_id": "a1", "domain": "code"},
            {"content": "beta content text here", "relevance": 0.8, "artifact_id": "a2", "domain": "finance"},
        ]
        reordered = mmr_reorder(results, "alpha")
        assert all("artifact_id" in r for r in reordered)
        assert all("domain" in r for r in reordered)


class TestCosineSimilarity:
    """Tests for cosine_similarity()."""

    def test_identical_vectors(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors_floored_at_zero(self):
        # Raw cosine would be -1.0; MMR's penalty term assumes [0, 1], so a
        # negative similarity is floored rather than inverting the penalty.
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == 0.0

    def test_empty_or_mismatched_inputs(self):
        assert cosine_similarity([], [1.0]) == 0.0
        assert cosine_similarity([1.0], []) == 0.0
        assert cosine_similarity([1.0, 0.0], [1.0]) == 0.0

    def test_zero_norm_vector(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


class TestMmrReorderEmbeddings:
    """Embedding-based MMR kernel: cosine over `"embedding"` when present,
    Jaccard fallback otherwise (Phase 2 item 2.7).

    Shared fixture: A is the top-relevance pick. B is a paraphrase of A —
    near-zero token overlap after stemming (so Jaccard sees no redundancy)
    but embedded at the *same* vector as A (so cosine sees full redundancy).
    C is a genuinely different topic, embedded orthogonally to A.
    """

    _RESULTS_NO_EMBEDDING = [
        {"content": "revenue climbed sharply overall quarter", "relevance": 0.95},
        {"content": "topline figures jumped considerably lately", "relevance": 0.90},
        {"content": "office relocated building downtown location", "relevance": 0.80},
    ]

    def _with_embeddings(self):
        results = [dict(r) for r in self._RESULTS_NO_EMBEDDING]
        results[0]["embedding"] = [1.0, 0.0]  # A
        results[1]["embedding"] = [1.0, 0.0]  # B — paraphrase of A, same embedding
        results[2]["embedding"] = [0.0, 1.0]  # C — orthogonal to A
        return results

    def test_jaccard_fallback_misses_the_paraphrase_duplicate(self):
        # No "embedding" key anywhere → pure Jaccard kernel (unchanged
        # pre-rework behavior). Jaccard(A, B) == Jaccard(A, C) == 0, so it
        # cannot tell the near-duplicate paraphrase B apart from the
        # genuinely distinct C — ranking falls back to pure relevance and
        # picks the redundant B over the diversifying C.
        reordered = mmr_reorder(self._RESULTS_NO_EMBEDDING, "revenue", lambda_param=0.5, top_n=2)
        contents = [r["content"] for r in reordered]
        assert contents[0].startswith("revenue")
        assert contents[1].startswith("topline")  # B (the redundant paraphrase) wins

    def test_embeddings_diversify_the_paraphrase_duplicate(self):
        # Same content/relevance, now with embeddings attached. Cosine
        # correctly identifies B as near-duplicate of A (same vector) and
        # penalizes it below C (orthogonal to A), which Jaccard could not see.
        reordered = mmr_reorder(self._with_embeddings(), "revenue", lambda_param=0.5, top_n=2)
        contents = [r["content"] for r in reordered]
        assert contents[0].startswith("revenue")
        assert contents[1].startswith("office")  # C (genuinely diverse) wins

    def test_partial_embedding_coverage_falls_back_per_pair(self):
        # Only A and C carry an embedding; B does not. The (A, B) comparison
        # must fall back to Jaccard (not crash, not treat B as redundant),
        # while (A, C) still uses cosine.
        results = self._with_embeddings()
        del results[1]["embedding"]  # B loses its embedding
        reordered = mmr_reorder(results, "revenue", lambda_param=0.5, top_n=2)
        contents = [r["content"] for r in reordered]
        assert contents[0].startswith("revenue")
        assert contents[1].startswith("topline")  # B: no embedding → Jaccard(A,B)=0 → wins on relevance
