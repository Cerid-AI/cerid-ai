# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verify backward-compatible imports after hallucination module decomposition.

Phase 36 Sprint 3 split the monolithic ``agents/hallucination.py`` into a
package with five submodules (patterns, extraction, verification, streaming,
persistence).  The ``core/agents/hallucination/__init__.py`` facade re-exports
all public and test-used symbols.  Private (underscore-prefixed) names must
be imported from the specific core submodule.
"""

from __future__ import annotations


class TestPatternSymbols:
    """Symbols from patterns.py."""

    def test_semaphore_getters(self):
        from core.agents.hallucination.patterns import (
            _get_claim_verify_semaphore,
            _get_ext_verify_semaphore,
        )
        assert callable(_get_claim_verify_semaphore)
        assert callable(_get_ext_verify_semaphore)

    def test_pattern_helpers(self):
        from core.agents.hallucination.patterns import (
            _has_staleness_indicators,
            _is_complex_claim,
            _is_current_event_claim,
            _is_ignorance_admission,
            _is_recency_claim,
        )
        assert callable(_has_staleness_indicators)
        assert callable(_is_complex_claim)
        assert callable(_is_current_event_claim)
        assert callable(_is_ignorance_admission)
        assert callable(_is_recency_claim)

    def test_model_helpers(self):
        from core.agents.hallucination.patterns import _model_family, _pick_verification_model
        assert callable(_model_family)
        assert callable(_pick_verification_model)


class TestExtractionSymbols:
    """Symbols from extraction.py."""

    def test_extract_claims(self):
        from agents.hallucination import extract_claims
        assert callable(extract_claims)

    def test_internal_extraction_functions(self):
        from core.agents.hallucination.extraction import (
            _detect_evasion,
            _extract_citation_claims,
            _extract_claims_heuristic,
            _extract_claims_llm,
        )
        assert callable(_detect_evasion)
        assert callable(_extract_citation_claims)
        assert callable(_extract_claims_heuristic)
        assert callable(_extract_claims_llm)


class TestVerificationSymbols:
    """Symbols from verification.py."""

    def test_verify_claim(self):
        from agents.hallucination import verify_claim
        assert callable(verify_claim)

    def test_system_prompts(self):
        from core.agents.hallucination.verification import (
            _SYSTEM_CITATION_VERIFICATION,
            _SYSTEM_CONSISTENCY_CHECK,
            _SYSTEM_CURRENT_EVENT_VERIFICATION,
            _SYSTEM_EVASION_VERIFICATION,
            _SYSTEM_IGNORANCE_VERIFICATION,
            _SYSTEM_RECENCY_VERIFICATION,
        )
        for prompt in [
            _SYSTEM_CITATION_VERIFICATION,
            _SYSTEM_CONSISTENCY_CHECK,
            _SYSTEM_CURRENT_EVENT_VERIFICATION,
            _SYSTEM_EVASION_VERIFICATION,
            _SYSTEM_IGNORANCE_VERIFICATION,
            _SYSTEM_RECENCY_VERIFICATION,
        ]:
            assert isinstance(prompt, str)
            assert len(prompt) > 50  # Non-trivial system prompts

    def test_verification_helpers(self):
        from core.agents.hallucination.verification import (
            _build_verification_details,
            _check_history_consistency,
            _check_numeric_alignment,
            _compute_adjusted_confidence,
            _interpret_recency_verdict,
            _invert_evasion_verdict,
            _invert_ignorance_verdict,
            _kb_source_fields,
            _llm_call_with_retry,
            _parse_verification_verdict,
            _query_memories,
            _verify_claim_externally,
        )
        for fn in [
            _build_verification_details,
            _check_history_consistency,
            _check_numeric_alignment,
            _compute_adjusted_confidence,
            _interpret_recency_verdict,
            _invert_evasion_verdict,
            _invert_ignorance_verdict,
            _kb_source_fields,
            _llm_call_with_retry,
            _parse_verification_verdict,
            _query_memories,
            _verify_claim_externally,
        ]:
            assert callable(fn)


class TestStreamingSymbols:
    """Symbols from streaming.py."""

    def test_check_hallucinations(self):
        from agents.hallucination import check_hallucinations
        assert callable(check_hallucinations)

    def test_verify_response_streaming(self):
        from agents.hallucination import verify_response_streaming
        assert callable(verify_response_streaming)


class TestPersistenceSymbols:
    """Symbols from persistence.py."""

    def test_get_hallucination_report(self):
        from agents.hallucination import get_hallucination_report
        assert callable(get_hallucination_report)

    def test_redis_constants(self):
        from agents.hallucination import (
            REDIS_HALLUCINATION_PREFIX,
            REDIS_HALLUCINATION_TTL,
        )
        assert isinstance(REDIS_HALLUCINATION_PREFIX, str)
        assert isinstance(REDIS_HALLUCINATION_TTL, int)
        assert REDIS_HALLUCINATION_TTL > 0


class TestSubmoduleAccess:
    """Verify that submodules are importable directly."""

    def test_import_patterns(self):
        import core.agents.hallucination.patterns as m
        assert hasattr(m, "_is_ignorance_admission")

    def test_import_extraction(self):
        import core.agents.hallucination.extraction as m
        assert hasattr(m, "extract_claims")

    def test_import_verification(self):
        import core.agents.hallucination.verification as m
        assert hasattr(m, "verify_claim")

    def test_import_streaming(self):
        import core.agents.hallucination.streaming as m
        assert hasattr(m, "verify_response_streaming")

    def test_import_persistence(self):
        import core.agents.hallucination.persistence as m
        assert hasattr(m, "get_hallucination_report")

    def test_httpx_accessible_on_package(self):
        """Tests rely on patching agents.hallucination.httpx.AsyncClient."""
        import agents.hallucination
        assert hasattr(agents.hallucination, "httpx")
