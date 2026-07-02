# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 2b slice 3 — ExternalEvidence normalises the three QUERY/verify shapes
and its to_authoritative_dict() reproduces the legacy authoritative_sources dict
byte-for-byte."""

from core.models.external_evidence import ExternalEvidence


class TestFromMappingNormalisation:
    def test_data_source_result_shape(self):
        # DataSourceResult.to_dict(): title/content/source_url/source_name/confidence
        ev = ExternalEvidence.from_mapping({
            "title": "Wikipedia",
            "content": "the body",
            "source_url": "https://en.wikipedia.org/x",
            "source_name": "Wikipedia",
            "confidence": 0.85,
        })
        assert ev.title == "Wikipedia"
        assert ev.content == "the body"
        assert ev.url == "https://en.wikipedia.org/x"
        assert ev.source_name == "Wikipedia"
        assert ev.relevance == 0.85
        assert ev.nli_entailment is None

    def test_web_search_result_shape(self):
        # WebSearchResult: title/url/snippet/score/published_date
        ev = ExternalEvidence.from_mapping({
            "title": "Tavily hit",
            "url": "https://example.com",
            "snippet": "a snippet",
            "score": 0.4,
            "published_date": "2026-01-01",
        })
        assert ev.content == "a snippet"       # snippet → content
        assert ev.url == "https://example.com"
        assert ev.relevance == 0.4             # score → relevance
        assert ev.published_date == "2026-01-01"

    def test_authoritative_dict_shape_roundtrip(self):
        # source/content/source_url/nli_*/data_freshness
        ev = ExternalEvidence.from_mapping({
            "source": "PubChem",
            "content": "chemistry",
            "source_url": "https://pubchem.example",
            "nli_entailment": 0.9,
            "nli_contradiction": 0.05,
            "data_freshness": "unknown",
        })
        assert ev.source_name == "PubChem"     # source → source_name
        assert ev.title == "PubChem"           # title falls back to source
        assert ev.nli_entailment == 0.9
        assert ev.nli_contradiction == 0.05
        assert ev.published_date is None       # "unknown" → None

    def test_absent_numeric_is_none_not_zero(self):
        """None (absent) must be distinguishable from a real 0.0 so consumers
        keep their own defaults."""
        assert ExternalEvidence.from_mapping({}).relevance is None
        assert ExternalEvidence.from_mapping({"confidence": 0.0}).relevance == 0.0

    def test_explicit_nli_kwargs_win(self):
        ev = ExternalEvidence.from_mapping(
            {"nli_entailment": 0.1}, nli_entailment=0.8, nli_contradiction=0.2,
        )
        assert ev.nli_entailment == 0.8
        assert ev.nli_contradiction == 0.2

    def test_guid_captured_when_present(self):
        assert ExternalEvidence.from_mapping({"guid": "abc"}).guid == "abc"
        assert ExternalEvidence.from_mapping({}).guid is None


def _legacy_authoritative_scored(ext: dict, entail: float, contra: float) -> dict:
    """The exact pre-refactor scored-source dict literal (authoritative_verify)."""
    content = ext.get("content", "")[:512]
    return {
        "source": ext.get("source_name", "unknown"),
        "content": content[:200],
        "source_url": ext.get("source_url", ""),
        "nli_entailment": entail,
        "nli_contradiction": contra,
        "data_freshness": ext.get("last_updated")
            or ext.get("data_freshness")
            or ext.get("published")
            or ext.get("retrieved_at", "unknown"),
    }


def _legacy_authoritative_fallback(ext: dict) -> dict:
    return {
        "source": ext.get("source_name", "unknown"),
        "content": ext.get("content", "")[:200],
        "source_url": ext.get("source_url", ""),
        "nli_entailment": 0.0,
        "nli_contradiction": 0.0,
        "data_freshness": ext.get("last_updated")
            or ext.get("data_freshness")
            or ext.get("published")
            or ext.get("retrieved_at", "unknown"),
    }


class TestAuthoritativeByteParity:
    _CASES = [
        {"source_name": "Wikipedia", "content": "x" * 400, "source_url": "https://w/x"},
        {"source_name": "PubChem", "content": "short", "source_url": ""},
        {"content": "no source name", "source_url": "https://y"},  # source_name absent
        {},                                                        # empty
        {"source_name": "Fresh", "content": "c", "data_freshness": "2026-06-01"},
    ]

    def test_scored_parity(self):
        for ext in self._CASES:
            got = ExternalEvidence.from_mapping(
                ext, nli_entailment=0.7, nli_contradiction=0.1,
            ).to_authoritative_dict()
            assert got == _legacy_authoritative_scored(ext, 0.7, 0.1), ext

    def test_fallback_parity(self):
        for ext in self._CASES:
            got = ExternalEvidence.from_mapping(ext).to_authoritative_dict()
            assert got == _legacy_authoritative_fallback(ext), ext


# Representative producer dicts (DataSourceResult.to_dict shape) for the
# relevance-bearing consumer sites (crag + orchestrator).
_PRODUCER_CASES = [
    {"title": "T", "content": "body", "source_url": "https://a", "source_name": "SN", "confidence": 0.85},
    {"content": "no name", "source_url": "https://b", "title": "OnlyTitle", "confidence": 0.0},  # real 0.0
    {"content": "no conf", "source_url": "", "source_name": "SN2"},  # confidence absent
    {},
]


class TestCragFieldParity:
    """crag builds SourceItem args from ev; must match the legacy r.get() exprs
    (confidence-absent default 0.8, a real 0.0 preserved)."""

    def test_relevance_and_names(self):
        from config.constants import EXTERNAL_SOURCE_RELEVANCE_DISCOUNT as D
        for r in _PRODUCER_CASES:
            ev = ExternalEvidence.from_mapping(r)
            rel = ev.relevance if ev.relevance is not None else 0.8
            assert round(rel * D, 3) == round(r.get("confidence", 0.8) * D, 3), r
            assert ev.content == r.get("content", "")
            assert ev.source_name == r.get("source_name", "")          # filename=
            assert ev.url == r.get("source_url", "")
            assert (ev.source_name or ev.title) == r.get("source_name", r.get("title", ""))


class TestOrchestratorFieldParity:
    """orchestrator external_sources dict; legacy default is 0.0 (not 0.8)."""

    def test_relevance_and_names(self):
        from config.constants import EXTERNAL_SOURCE_RELEVANCE_DISCOUNT as D
        for r in _PRODUCER_CASES:
            ev = ExternalEvidence.from_mapping(r)
            rel = ev.relevance if ev.relevance is not None else 0.0
            legacy_conf = r.get("confidence", r.get("relevance", 0.0))
            assert round(rel * D, 3) == round(legacy_conf * D, 3), r
            assert ev.content == r.get("content", "")
            assert ev.url == r.get("source_url", "")
            assert (ev.source_name or ev.title) == r.get("source_name", r.get("title", ""))
