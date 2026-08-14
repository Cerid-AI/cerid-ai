# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Meta/self-referential statements must never become verification claims.

UX-09 (sf-2-honest-degradation): chat streamed the ungrounded denial
"I don't have access to your Apple Mail", verification extracted it as an
ignorance claim, and the report stamped the false denial "1/1 verified,
100% accuracy". Statements about the assistant itself — its identity, or
its access to the user's private data — have no external referent a
verifier could check, so extraction must surface zero claims from them.
World-facing ignorance admissions keep flowing to the ignorance-verdict
path unchanged.
"""

from unittest.mock import patch

import pytest

from core.agents.hallucination.extraction import (
    _extract_ignorance_claims,
    extract_claims,
)
from core.agents.hallucination.patterns import is_meta_self_referential

# The drive report's exact abstention shape (UX-09 acceptance text).
META_ABSTENTION = (
    "I'm a large language model, and I don't have access to your Apple Mail "
    "or any of your personal data. I cannot read your emails."
)


class TestMetaPredicate:
    def test_identity_statements_are_meta(self):
        for s in [
            "I'm a large language model trained to answer questions.",
            "I am an AI assistant without real-time awareness.",
            "As an AI, I cannot form personal opinions.",
        ]:
            assert is_meta_self_referential(s), s

    def test_user_data_access_denials_are_meta(self):
        for s in [
            "I don't have access to your Apple Mail account.",
            "I cannot access your personal information or files.",
            "I am unable to access the user's calendar entries.",
            "I can't read your emails or messages directly.",
        ]:
            assert is_meta_self_referential(s), s

    def test_world_facing_ignorance_is_not_meta(self):
        for s in [
            "I don't have information about the 2031 census results.",
            "I cannot recall who painted the Mona Lisa.",
            "There is no reliable information about the expedition's fate.",
            "I don't have access to data from the 2027 fiscal filings.",
        ]:
            assert not is_meta_self_referential(s), s


class TestIgnorancePreExtractionSkipsMeta:
    def test_meta_denial_yields_no_ignorance_claims(self):
        assert _extract_ignorance_claims(META_ABSTENTION) == []

    def test_world_facing_ignorance_still_surfaces(self):
        claims = _extract_ignorance_claims(
            "I don't have information about the 2031 census results."
        )
        assert len(claims) == 1


class TestExtractClaimsMetaFilter:
    @pytest.mark.asyncio
    async def test_exact_meta_abstention_extracts_zero_claims(self):
        """The UX-09 acceptance probe: the exact 'I'm a large language
        model…' abstention must extract zero claims even when the LLM
        extractor parrots the meta sentence back as a claim."""
        with patch(
            "core.agents.hallucination.extraction._extract_claims_llm",
            return_value=[
                "I don't have access to your Apple Mail or any of your personal data",
            ],
        ):
            claims, method = await extract_claims(
                META_ABSTENTION,
                user_query="what invoices arrived in my mail this week?",
            )
        assert claims == []
        assert method == "none"

    @pytest.mark.asyncio
    async def test_factual_claims_survive_alongside_meta(self):
        """A response mixing a meta disclaimer with a real factual claim
        keeps the factual claim and drops only the meta one."""
        with patch(
            "core.agents.hallucination.extraction._extract_claims_llm",
            return_value=[
                "I'm a large language model without access to your files",
                "The Eiffel Tower is 330 meters tall",
            ],
        ):
            claims, method = await extract_claims(
                "I'm a large language model without access to your files. "
                "The Eiffel Tower is 330 meters tall.",
            )
        assert claims == ["The Eiffel Tower is 330 meters tall"]
        assert method == "llm"
