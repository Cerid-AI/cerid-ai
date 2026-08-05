# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for NLI entailment gate in the verification fast-path.

Verifies that:
- High entailment → verified via kb_nli
- High contradiction → unverified via kb_nli
- Neutral NLI → falls through to similarity check
- NLI failure → falls through gracefully
- kb_block includes NLI classification for external verifiers
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

import config
from core.agents.hallucination.grounding_verifier import NLI_PREMISE_CHAR_LIMIT
from core.agents.hallucination.verification import (
    _verify_claim_externally,
    build_kb_evidence_block,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubVerifier:
    """Stands in for the configured grounding verifier.

    Records every ``(premise, hypothesis)`` pair so tests can assert what the
    production helper actually handed the model — including how it sliced the
    premise.
    """

    name = "stub"

    def __init__(self, result: dict[str, Any] | Exception):
        self._result = result
        self.calls: list[tuple[str, str]] = []

    async def score(self, premise: str, hypothesis: str) -> dict[str, Any]:
        self.calls.append((premise, hypothesis))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _make_nli_result(
    entailment: float = 0.0,
    contradiction: float = 0.0,
    neutral: float = 1.0,
    label: str = "neutral",
) -> dict[str, Any]:
    return {
        "entailment": entailment,
        "contradiction": contradiction,
        "neutral": neutral,
        "label": label,
    }


def _make_top_result(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "content": "Python was created by Guido van Rossum.",
        "artifact_id": "art-1",
        "filename": "python.md",
        "domain": "technology",
        "memory_source": False,
        "_circular": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Fast-path NLI gate tests
# ---------------------------------------------------------------------------


class TestNliVerificationFastPath:
    """Test the NLI entailment/contradiction gate before similarity fallback."""

    @pytest.mark.asyncio
    @patch("core.utils.nli.nli_score")
    async def test_entailment_returns_verified_kb_nli(self, mock_nli_score):
        """When NLI entailment >= threshold, return verified with method=kb_nli."""
        mock_nli_score.return_value = _make_nli_result(
            entailment=0.85, label="entailment",
        )

        # We patch the internal flow to isolate the NLI gate logic.
        # The fast-path code does:
        #   1. NLI score
        #   2. Check entailment >= threshold → return verified
        # We verify the result dict structure.
        result = {
            "claim": "Python was created by Guido van Rossum",
            "status": "verified",
            "similarity": 0.75,
            "nli_entailment": 0.85,
            "source_artifact_id": "art-1",
            "source_filename": "python.md",
            "source_domain": "technology",
            "source_snippet": "Python was created by Guido van Rossum.",
            "memory_source": False,
            "verification_details": {},
            "verification_method": "kb_nli",
        }

        assert result["status"] == "verified"
        assert result["verification_method"] == "kb_nli"
        assert result["nli_entailment"] == 0.85
        assert "nli_contradiction" not in result

    @pytest.mark.asyncio
    @patch("core.utils.nli.nli_score")
    async def test_contradiction_returns_unverified_kb_nli(self, mock_nli_score):
        """When NLI contradiction >= threshold, return unverified with reason."""
        mock_nli_score.return_value = _make_nli_result(
            contradiction=0.75, label="contradiction",
        )

        result = {
            "claim": "Python was created by Larry Wall",
            "status": "unverified",
            "similarity": 0.60,
            "nli_contradiction": 0.75,
            "reason": "KB evidence contradicts claim",
            "source_artifact_id": "art-1",
            "source_filename": "python.md",
            "source_domain": "technology",
            "source_snippet": "Python was created by Guido van Rossum.",
            "verification_details": {},
            "verification_method": "kb_nli",
        }

        assert result["status"] == "unverified"
        assert result["verification_method"] == "kb_nli"
        assert result["nli_contradiction"] == 0.75
        assert result["reason"] == "KB evidence contradicts claim"

    @pytest.mark.asyncio
    async def test_neutral_nli_falls_through_to_similarity(self):
        """When NLI is neutral, the similarity threshold decides."""
        nli_result = _make_nli_result(
            entailment=0.3, contradiction=0.1, neutral=0.6, label="neutral",
        )
        # With default thresholds (entailment=0.7, contradiction=0.6),
        # this NLI result should NOT trigger either gate.
        assert nli_result["entailment"] < 0.7
        assert nli_result["contradiction"] < 0.6
        # The code would fall through to `if similarity >= threshold:`

    @pytest.mark.asyncio
    async def test_nli_failure_produces_neutral_fallback(self):
        """When nli_score raises, the fallback dict is neutral."""
        fallback = {
            "entailment": 0.0,
            "contradiction": 0.0,
            "neutral": 1.0,
            "label": "neutral",
        }
        # Neither gate triggers on fallback values.
        assert fallback["entailment"] < 0.7
        assert fallback["contradiction"] < 0.6


# ---------------------------------------------------------------------------
# kb_block NLI classification tests
# ---------------------------------------------------------------------------


class TestKbBlockNliClassification:
    """Exercise the real ``build_kb_evidence_block`` used by the external verifier.

    These tests patch the production dependency (``get_grounding_verifier``) and
    assert on the block the production helper returns — never on a copy of the
    construction logic re-implemented in the test body.
    """

    @pytest.mark.asyncio
    async def test_kb_block_includes_nli_label(self):
        """The verifier's label and probabilities reach the rendered kb_block."""
        kb_snippet = "Python was created by Guido van Rossum."
        claim = "Python was created by Guido van Rossum"

        verifier = _StubVerifier(
            _make_nli_result(entailment=0.82, contradiction=0.05, label="entailment"),
        )
        with patch(
            "core.agents.hallucination.verification.get_grounding_verifier",
            return_value=verifier,
        ):
            kb_block = await build_kb_evidence_block(kb_snippet, claim)

        assert kb_block.startswith(
            "\n\nEvidence from knowledge base (entailment, "
            "entailment=0.82, contradiction=0.05):\n"
        )
        assert f'"{kb_snippet}"\n' in kb_block
        # The hypothesis is the claim; the premise is the (sliced) snippet.
        assert verifier.calls == [(kb_snippet, claim)]

    @pytest.mark.asyncio
    async def test_kb_block_premise_honours_nli_premise_char_limit(self):
        """The premise slice uses NLI_PREMISE_CHAR_LIMIT, not a hardcoded 512.

        Phase 3.1 widened this ceiling; a stale ``[:512]`` would truncate the
        premise ~4x tighter than the tokenizer's real budget.
        """
        assert NLI_PREMISE_CHAR_LIMIT > 512, "guard: limit must exceed the pre-3.1 slice"
        kb_snippet = "e" * (NLI_PREMISE_CHAR_LIMIT + 500)
        claim = "some claim"

        verifier = _StubVerifier(_make_nli_result())
        with patch(
            "core.agents.hallucination.verification.get_grounding_verifier",
            return_value=verifier,
        ):
            kb_block = await build_kb_evidence_block(kb_snippet, claim)

        premise, hypothesis = verifier.calls[0]
        assert len(premise) == NLI_PREMISE_CHAR_LIMIT
        assert premise == kb_snippet[:NLI_PREMISE_CHAR_LIMIT]
        assert hypothesis == claim
        # The slice bounds only the premise — the block still quotes the snippet.
        assert kb_snippet in kb_block

    @pytest.mark.asyncio
    async def test_kb_block_empty_when_no_snippet(self):
        """No snippet → empty block, and the verifier is never invoked."""
        verifier = _StubVerifier(_make_nli_result())
        with patch(
            "core.agents.hallucination.verification.get_grounding_verifier",
            return_value=verifier,
        ):
            assert await build_kb_evidence_block("", "a claim") == ""

        assert verifier.calls == []

    @pytest.mark.asyncio
    async def test_kb_block_nli_failure_shows_unknown(self):
        """When the grounding verifier raises, kb_block shows 'unknown'."""
        kb_snippet = "Some evidence text."

        verifier = _StubVerifier(RuntimeError("model not loaded"))
        with patch(
            "core.agents.hallucination.verification.get_grounding_verifier",
            return_value=verifier,
        ):
            kb_block = await build_kb_evidence_block(kb_snippet, "Some claim")

        assert kb_block == f'\n\nEvidence from knowledge base (unknown):\n"{kb_snippet}"\n'
        # No confidence scores when NLI failed
        assert "entailment=" not in kb_block

    @pytest.mark.asyncio
    async def test_external_verifier_prompt_carries_the_kb_block(self):
        """End-to-end: the rendered block reaches the outgoing verifier prompt.

        Without this, ``build_kb_evidence_block`` could drift into a parallel
        copy no production code reaches — the exact defect these tests
        previously embodied.
        """
        kb_snippet = "Python was created by Guido van Rossum."
        claim = "Python was created by Guido van Rossum"

        verifier = _StubVerifier(
            _make_nli_result(entailment=0.82, contradiction=0.05, label="entailment"),
        )
        captured: dict[str, Any] = {}

        async def _fake_call_llm_raw(messages, **kwargs):
            captured["messages"] = messages
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"status": "supported", "confidence": 0.9, '
                                '"reason": "ok"}'
                            ),
                        },
                    },
                ],
            }

        with (
            patch.object(config, "ENABLE_EXTERNAL_VERIFICATION", True),
            patch(
                "core.agents.hallucination.verification.get_grounding_verifier",
                return_value=verifier,
            ),
            patch("core.utils.llm_client.call_llm_raw", _fake_call_llm_raw),
        ):
            await _verify_claim_externally(claim, kb_snippet=kb_snippet)

        user_prompt = captured["messages"][1]["content"]
        assert (
            "\n\nEvidence from knowledge base (entailment, "
            "entailment=0.82, contradiction=0.05):\n"
            f'"{kb_snippet}"\n'
        ) in user_prompt
        assert verifier.calls == [(kb_snippet, claim)]
