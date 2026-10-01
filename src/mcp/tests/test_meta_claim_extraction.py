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

from unittest.mock import AsyncMock, patch

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


# ---------------------------------------------------------------------------
# Refusals and statements of inability about the user's own sources
# ---------------------------------------------------------------------------

# The answer that was shown "1/1 verified, Accuracy: 100%, cross-model
# (100% match)".
OBSERVED_REFUSAL = "I'm sorry, but I don't have access to your documents."

REFUSALS = [
    OBSERVED_REFUSAL,
    "I’m sorry, but I don’t have access to your documents.",
    "I can't find that in your knowledge base.",
    "I couldn't find anything about the lease terms in your documents.",
    "I'm unable to find any information about that in the provided context.",
    "I was unable to locate the lease agreement in your knowledge base.",
    "I do not have access to any documents or files.",
    "I don't have access to the documents you mentioned.",
    "I don't have information about that.",
    "Sorry, I don't have any information on that topic.",
    "I don't have information about the lease terms in your knowledge base.",
    "I don't have enough information to answer that question.",
    "The provided context does not contain information about the lease terms.",
    "Your knowledge base doesn't contain anything about the lease terms.",
]

# Sentences that share words with a refusal and are claims about the world.
FACTUAL = [
    "The API does not have access control by default.",
    "Users don't have access to the admin panel until 2024.",
    "Guest accounts cannot access the billing page in version 3.2.",
    "The knowledge base article was published in 2019 and covers 40 topics.",
    "Researchers couldn't find a link between the drug and the 2019 outbreak.",
    "The documents were signed in Paris in 1783.",
    "The context manager closes the file after 3 retries.",
    "Your files do not include executable permissions by default on FAT32.",
]

USER_QUERY = "What does my lease say about pets?"


async def _first_streaming_event(response_text: str, llm_claims: list[str]) -> dict:
    """First event of the streaming verifier, with only the model call
    replaced: the extractor model returns *llm_claims*."""
    from core.agents.hallucination.streaming import verify_response_streaming

    with patch(
        "core.agents.hallucination.streaming._extract_claims_llm",
        new_callable=AsyncMock,
        return_value=llm_claims,
    ):
        gen = verify_response_streaming(
            response_text, "conv-refusal", None, None, None,
            user_query=USER_QUERY,
        )
        try:
            return await gen.__anext__()
        finally:
            await gen.aclose()


class TestRefusalPredicate:
    @pytest.mark.parametrize("sentence", REFUSALS)
    def test_refusal_is_meta(self, sentence):
        assert is_meta_self_referential(sentence)

    @pytest.mark.parametrize("sentence", FACTUAL)
    def test_factual_sentence_is_not_meta(self, sentence):
        assert not is_meta_self_referential(sentence)

    def test_ignorance_of_a_named_topic_is_not_meta(self):
        for s in [
            "I don't have information about the 2031 census results.",
            "I couldn't find a 2019 study in the Cochrane database.",
            "I don't have enough information about the Treaty of Paris.",
        ]:
            assert not is_meta_self_referential(s), s


class TestRefusalExtractsNoClaim:
    @pytest.mark.parametrize("refusal", REFUSALS)
    def test_ignorance_pre_extraction_yields_nothing(self, refusal):
        assert _extract_ignorance_claims(refusal) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("refusal", REFUSALS)
    async def test_extract_claims_yields_nothing(self, refusal):
        with patch(
            "core.agents.hallucination.extraction._extract_claims_llm",
            return_value=[refusal.rstrip(".")],
        ):
            claims, method = await extract_claims(refusal, user_query=USER_QUERY)
        assert claims == []
        assert method == "none"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("refusal", REFUSALS)
    async def test_streaming_verifier_yields_nothing(self, refusal):
        event = await _first_streaming_event(refusal, [refusal.rstrip(".")])
        assert event["type"] == "summary", event
        assert event["skipped"] is True
        assert event["total"] == 0


class TestFactualClaimsStillExtracted:
    @pytest.mark.parametrize("sentence", FACTUAL)
    def test_heuristic_claim_survives(self, sentence):
        from core.agents.hallucination.extraction import _extract_claims_heuristic

        assert _extract_claims_heuristic(sentence) == [sentence.rstrip(".")]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sentence", FACTUAL)
    async def test_extract_claims_keeps_it(self, sentence):
        with patch(
            "core.agents.hallucination.extraction._extract_claims_llm",
            return_value=[sentence.rstrip(".")],
        ):
            claims, method = await extract_claims(sentence, user_query=USER_QUERY)
        assert claims == [sentence.rstrip(".")]
        assert method == "llm"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sentence", FACTUAL)
    async def test_streaming_verifier_keeps_it(self, sentence):
        event = await _first_streaming_event(sentence, [sentence.rstrip(".")])
        assert event == {"type": "extraction_complete", "method": "heuristic", "count": 1}

    @pytest.mark.asyncio
    async def test_streaming_keeps_the_fact_beside_a_refusal(self):
        text = (
            "I'm sorry, but I don't have access to your documents. "
            "The Eiffel Tower is 330 meters tall."
        )
        event = await _first_streaming_event(text, [])
        assert event == {"type": "extraction_complete", "method": "heuristic", "count": 1}


# ---------------------------------------------------------------------------
# The refusal is removed before any extractor reads the response
# ---------------------------------------------------------------------------

# What the extractor model returned for OBSERVED_REFUSAL on the live stack
# (2026-09-30): the refusal restated in the third person, which no
# first-person meta pattern matches once it is a claim.
PARAPHRASED_REFUSAL = "The AI model does not have access to the user's documents."

MIXED = f"{OBSERVED_REFUSAL} The Eiffel Tower is 330 meters tall."
EIFFEL_CLAIM = "The Eiffel Tower is 330 meters tall"
# The heuristic finds no claim in this sentence, so the streaming verifier
# has to ask the model.
MIXED_MODEL_ONLY = f"{OBSERVED_REFUSAL} The Louvre houses the Mona Lisa."
LOUVRE_CLAIM = "The Louvre houses the Mona Lisa"


class _ParaphrasingExtractor:
    """Stands in for the extractor model and nothing else: it restates the
    refusal in the third person as the live model did, and returns every
    other sentence as a claim."""

    def __init__(self) -> None:
        self.received: list[str] = []

    async def __call__(self, response_text, max_claims, user_query=None):
        from core.agents.hallucination.patterns import SENTENCE_RE

        self.received.append(response_text)
        claims = []
        for sentence in SENTENCE_RE.split(response_text.strip()):
            if "access to your documents" in sentence.replace("’", "'"):
                claims.append(PARAPHRASED_REFUSAL)
            elif sentence:
                claims.append(sentence.rstrip("."))
        return claims[:max_claims]


async def _streamed_extraction(response_text: str, extractor) -> tuple[dict, list[str]]:
    """The streaming verifier's extraction events, with only the model
    extractor replaced: (extraction_complete or summary event, claims)."""
    from core.agents.hallucination.streaming import verify_response_streaming

    with patch("core.agents.hallucination.streaming._extract_claims_llm", new=extractor):
        gen = verify_response_streaming(
            response_text, "conv-refusal", None, None, None, user_query=USER_QUERY,
        )
        try:
            first = await gen.__anext__()
            claims: list[str] = []
            if first["type"] == "extraction_complete":
                for _ in range(first["count"]):
                    claims.append((await gen.__anext__())["claim"])
            return first, claims
        finally:
            await gen.aclose()


REFUSAL_FORMS = [OBSERVED_REFUSAL, "I’m sorry, but I don’t have access to your documents."]


class TestRefusalRemovedBeforeExtraction:
    @pytest.mark.parametrize("refusal", REFUSAL_FORMS)
    async def test_extract_claims_never_asks_the_model(self, refusal):
        fake = _ParaphrasingExtractor()
        with patch("core.agents.hallucination.extraction._extract_claims_llm", new=fake):
            claims, method = await extract_claims(refusal, user_query=USER_QUERY)
        assert claims == []
        assert method == "none"
        assert fake.received == []

    @pytest.mark.parametrize("refusal", REFUSAL_FORMS)
    async def test_streaming_verifier_never_asks_the_model(self, refusal):
        fake = _ParaphrasingExtractor()
        event, claims = await _streamed_extraction(refusal, fake)
        assert event["type"] == "summary", event
        assert event["skipped"] is True
        assert event["total"] == 0
        assert event["extraction_method"] == "none"
        assert fake.received == []

    async def test_extract_claims_model_reads_only_the_fact(self):
        fake = _ParaphrasingExtractor()
        with patch("core.agents.hallucination.extraction._extract_claims_llm", new=fake):
            claims, method = await extract_claims(MIXED, user_query=USER_QUERY)
        assert claims == [EIFFEL_CLAIM]
        assert method == "llm"
        assert fake.received == ["The Eiffel Tower is 330 meters tall."]

    async def test_streaming_verifier_keeps_only_the_fact(self):
        fake = _ParaphrasingExtractor()
        event, claims = await _streamed_extraction(MIXED, fake)
        assert claims == [EIFFEL_CLAIM]
        assert all("access to your documents" not in text for text in fake.received)

    async def test_streaming_model_path_reads_only_the_fact(self):
        fake = _ParaphrasingExtractor()
        event, claims = await _streamed_extraction(MIXED_MODEL_ONLY, fake)
        assert event == {"type": "extraction_complete", "method": "llm", "count": 1}
        assert claims == [LOUVRE_CLAIM]
        assert fake.received == ["The Louvre houses the Mona Lisa."]

    @pytest.mark.parametrize("sentence", FACTUAL)
    async def test_model_reads_a_factual_response_unchanged(self, sentence):
        fake = _ParaphrasingExtractor()
        with patch("core.agents.hallucination.extraction._extract_claims_llm", new=fake):
            claims, method = await extract_claims(sentence, user_query=USER_QUERY)
        assert fake.received == [sentence]
        assert claims == [sentence.rstrip(".")]
        assert method == "llm"


class _NullRedis:
    def get(self, _key):
        return None

    def __getattr__(self, _name):
        return lambda *a, **k: None


class TestClaimsLocatedInTheOriginalResponse:
    """Claims are located in the response the user saw: the web app marks a
    claim inline by finding its text in the message, and the verifier is
    handed the text around the claim. Both must index the original response,
    refusal included, not the text the extractor read."""

    @staticmethod
    def _assert_located_in_original(claim: str, claim_context: str | None) -> None:
        start = MIXED.index("The Eiffel Tower")
        assert MIXED[start:start + len(claim)] == claim
        assert MIXED.lower().find(claim.lower()) == start
        assert claim_context is not None
        assert claim_context in MIXED
        assert OBSERVED_REFUSAL in claim_context

    async def test_streaming_verifier(self):
        from core.agents.hallucination.streaming import verify_response_streaming

        seen: dict = {}

        async def _verify_claim(claim_text, *a, **k):
            seen[claim_text] = k.get("claim_context")
            return {"status": "verified", "similarity": 0.9, "verification_method": "kb"}

        fake = _ParaphrasingExtractor()
        with (
            patch("core.agents.hallucination.streaming._extract_claims_llm", new=fake),
            patch("core.agents.hallucination.streaming.verify_claim", side_effect=_verify_claim),
        ):
            events = [
                ev async for ev in verify_response_streaming(
                    MIXED, "conv-span", None, None, _NullRedis(), user_query=USER_QUERY,
                )
            ]
        claims = [ev["claim"] for ev in events if ev["type"] == "claim_extracted"]
        assert claims == [EIFFEL_CLAIM]
        self._assert_located_in_original(EIFFEL_CLAIM, seen[EIFFEL_CLAIM])

    async def test_non_streaming_verifier(self):
        from core.agents.hallucination.streaming import check_hallucinations

        seen: dict = {}

        async def _verify_claim(claim_text, *a, **k):
            seen[claim_text] = k.get("claim_context")
            return {"claim": claim_text, "status": "verified", "similarity": 0.9}

        fake = _ParaphrasingExtractor()
        with (
            patch("core.agents.hallucination.extraction._extract_claims_llm", new=fake),
            patch("core.agents.hallucination.streaming.verify_claim", side_effect=_verify_claim),
        ):
            report = await check_hallucinations(
                MIXED, "conv-span", None, None, _NullRedis(), user_query=USER_QUERY,
            )
        assert [c["claim"] for c in report["claims"]] == [EIFFEL_CLAIM]
        self._assert_located_in_original(EIFFEL_CLAIM, seen[EIFFEL_CLAIM])


# ---------------------------------------------------------------------------
# A sentence that mixes a meta clause with a fact keeps the fact
# ---------------------------------------------------------------------------

# Removing these sentences whole would hide the fact from verification, and
# with it any hallucination written beside a disclaimer.
MIXED_SENTENCES = [
    "As an AI, I can tell you the Eiffel Tower is 330 meters tall.",
    "I don't have access to your documents, but the Eiffel Tower is 330 meters tall.",
    "I’m sorry, but I don’t have access to your documents; however, the Eiffel Tower is 330 meters tall.",
]
# The fact as written in each of them.
EIFFEL_TAIL = "the Eiffel Tower is 330 meters tall"
TWO_FACTS = (
    "The Eiffel Tower is 330 meters tall; I don't have access to your documents, "
    "but the Eiffel Tower was completed in 1889."
)
COMPLETED_CLAIM = "The Eiffel Tower was completed in 1889"
META_ONLY = [
    "As an AI language model, I cannot browse the internet.",
    "I'm a large language model, and I don't have access to your Apple Mail or any of your personal data.",
    # After a comma the rest of the refusal's object is not a new clause.
    "I don't have access to your emails, calendar, or the 2023 tax filings you uploaded.",
    "I don't have access to your Apple Mail, Calendar, or Contacts from March 2023.",
]


class _MixedSentenceExtractor:
    """Stands in for the extractor model and nothing else. Shown a meta
    clause it restates it in the third person, as the live model did, which
    the first-person patterns no longer match; shown a fact it returns the
    fact as a self-contained claim."""

    FACTS = {
        "Eiffel Tower is 330 meters tall": EIFFEL_CLAIM,
        "Eiffel Tower was completed in 1889": COMPLETED_CLAIM,
    }

    def __init__(self) -> None:
        self.received: list[str] = []

    async def __call__(self, response_text, max_claims, user_query=None):
        self.received.append(response_text)
        text = response_text.replace("’", "'")
        claims = []
        if "access to your documents" in text:
            claims.append(PARAPHRASED_REFUSAL)
        if "As an AI" in text:
            claims.append("The assistant is an AI language model")
        claims.extend(claim for fact, claim in self.FACTS.items() if fact in text)
        return claims[:max_claims]


class TestMetaClauseRemovedFactKept:
    @pytest.mark.parametrize("sentence", MIXED_SENTENCES)
    async def test_extract_claims_model_reads_only_the_fact(self, sentence):
        fake = _MixedSentenceExtractor()
        with patch("core.agents.hallucination.extraction._extract_claims_llm", new=fake):
            claims, method = await extract_claims(sentence, user_query=USER_QUERY)
        assert claims == [EIFFEL_CLAIM]
        assert method == "llm"
        assert len(fake.received) == 1
        assert fake.received[0].strip() in sentence
        assert not is_meta_self_referential(fake.received[0])

    @pytest.mark.parametrize("sentence", MIXED_SENTENCES)
    async def test_extract_claims_heuristic_keeps_the_fact_as_written(self, sentence):
        with patch(
            "core.agents.hallucination.extraction._extract_claims_llm",
            new_callable=AsyncMock,
            return_value=[],
        ):
            claims, method = await extract_claims(sentence, user_query=USER_QUERY)
        assert claims == [EIFFEL_TAIL]
        assert method == "heuristic"
        assert all(claim in sentence for claim in claims)

    @pytest.mark.parametrize("sentence", MIXED_SENTENCES)
    async def test_streaming_verifier_keeps_the_fact_as_written(self, sentence):
        fake = _MixedSentenceExtractor()
        event, claims = await _streamed_extraction(sentence, fake)
        assert event == {"type": "extraction_complete", "method": "heuristic", "count": 1}
        assert claims == [EIFFEL_TAIL]
        assert all(claim in sentence for claim in claims)

    async def test_both_facts_around_a_meta_clause_survive(self):
        fake = _MixedSentenceExtractor()
        with patch("core.agents.hallucination.extraction._extract_claims_llm", new=fake):
            claims, method = await extract_claims(TWO_FACTS, user_query=USER_QUERY)
        assert claims == [EIFFEL_CLAIM, COMPLETED_CLAIM]
        assert method == "llm"
        assert all(part.strip() in TWO_FACTS for part in fake.received[0].split("\n"))

    async def test_streaming_keeps_both_facts_as_written(self):
        fake = _MixedSentenceExtractor()
        event, claims = await _streamed_extraction(TWO_FACTS, fake)
        assert claims == [
            "The Eiffel Tower is 330 meters tall",
            "the Eiffel Tower was completed in 1889",
        ]
        assert all(claim in TWO_FACTS for claim in claims)

    async def test_streaming_fact_is_located_in_the_original(self):
        from core.agents.hallucination.streaming import verify_response_streaming

        sentence = MIXED_SENTENCES[1]
        seen: dict = {}

        async def _verify_claim(claim_text, *a, **k):
            seen[claim_text] = k.get("claim_context")
            return {"status": "verified", "similarity": 0.9, "verification_method": "kb"}

        with (
            patch(
                "core.agents.hallucination.streaming._extract_claims_llm",
                new=_MixedSentenceExtractor(),
            ),
            patch("core.agents.hallucination.streaming.verify_claim", side_effect=_verify_claim),
        ):
            [ev async for ev in verify_response_streaming(
                sentence, "conv-clause", None, None, _NullRedis(), user_query=USER_QUERY,
            )]
        assert list(seen) == [EIFFEL_TAIL]
        assert seen[EIFFEL_TAIL] is not None
        assert seen[EIFFEL_TAIL] in sentence

    @pytest.mark.parametrize("sentence", META_ONLY)
    async def test_meta_only_sentence_reaches_no_extractor(self, sentence):
        fake = _MixedSentenceExtractor()
        with patch("core.agents.hallucination.extraction._extract_claims_llm", new=fake):
            claims, method = await extract_claims(sentence, user_query=USER_QUERY)
        assert claims == []
        assert method == "none"
        assert fake.received == []

    @pytest.mark.parametrize("sentence", META_ONLY)
    async def test_meta_only_sentence_streams_nothing(self, sentence):
        fake = _MixedSentenceExtractor()
        event, claims = await _streamed_extraction(sentence, fake)
        assert event["type"] == "summary", event
        assert event["total"] == 0
        assert fake.received == []
