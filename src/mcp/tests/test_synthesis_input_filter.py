# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the RAG C3.3 synthesis-input filter.

Brief generation + weekly synthesis MUST exclude Claims whose upstream
Artifact carries ``source_type="cerid-synthesis"`` so Cerid's own
outputs don't feed back into the next synthesis pass.  Claims with
``cerid_reanalyze=true`` on the source Artifact re-enter the input set.

The Cypher query is exercised via a fake driver — we verify that:

1. The WHERE clause names the loop-breaker properties (source_type,
   cerid_reanalyze, EXTRACTED_FROM).
2. With a Claim+Artifact dataset, the assembled corpus excludes the
   cerid-synthesis claim by default.
3. The cerid-synthesis claim re-enters the corpus when
   ``cerid_reanalyze=true`` is set on the source artifact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Minimal Neo4j session/driver fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeClaim:
    """One :Claim with an optional linked :Artifact."""

    claim_id: str
    text: str
    source_type: str | None = None  # Artifact.source_type or None
    cerid_reanalyze: bool = False
    has_artifact: bool = True  # set False to simulate orphan claim


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def data(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _FakeSession:
    """Implements ``run(cypher, **params)`` against a small in-memory dataset."""

    def __init__(self, claims: list[FakeClaim], briefs: list[dict[str, Any]]):
        self._claims = claims
        self._briefs = briefs
        self.last_cypher: str | None = None

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *args) -> None:
        pass

    def run(self, cypher: str, **params: Any) -> _FakeResult:
        self.last_cypher = cypher
        if "MATCH (b:Brief" in cypher:
            return _FakeResult(list(self._briefs))
        if "MATCH (c:Claim" in cypher:
            # Apply the loop-breaker filter the production Cypher would
            # apply server-side.  The check mirrors the WHERE clause:
            # the claim flows through if no artifact is linked, the
            # linked artifact isn't cerid-synthesis, OR cerid_reanalyze
            # is True.
            out: list[dict[str, Any]] = []
            for c in self._claims:
                excluded_by_filter = (
                    c.has_artifact
                    and c.source_type == "cerid-synthesis"
                    and not c.cerid_reanalyze
                )
                if excluded_by_filter:
                    continue
                out.append({"text": c.text, "claim_id": c.claim_id})
            return _FakeResult(out)
        return _FakeResult([])


class _FakeDriver:
    def __init__(self, claims: list[FakeClaim], briefs: list[dict[str, Any]]):
        self.session_obj = _FakeSession(claims, briefs)

    def session(self) -> _FakeSession:
        return self.session_obj


# ---------------------------------------------------------------------------
# Cypher contract — the WHERE clause must reference the loop-breaker properties
# ---------------------------------------------------------------------------


class TestCypherContract:
    def test_brief_generation_cypher_filters_cerid_synthesis(self):
        from app.processor.jobs.brief_generation import _assemble_corpus

        driver = _FakeDriver(claims=[], briefs=[])
        _assemble_corpus(driver, "2026-05-10")
        cypher = driver.session_obj.last_cypher or ""
        assert "EXTRACTED_FROM" in cypher
        assert "source_type" in cypher
        assert "cerid-synthesis" in cypher
        assert "cerid_reanalyze" in cypher

    def test_weekly_synthesis_cypher_filters_cerid_synthesis(self):
        from app.processor.jobs.weekly_synthesis import _build_vault_snapshot

        driver = _FakeDriver(claims=[], briefs=[])
        _build_vault_snapshot(driver, "2026-05-11")
        cypher = driver.session_obj.last_cypher or ""
        assert "EXTRACTED_FROM" in cypher
        assert "source_type" in cypher
        assert "cerid-synthesis" in cypher
        assert "cerid_reanalyze" in cypher

    def test_brief_cypher_uses_optional_match(self):
        """Orphan claims (no linked Artifact) must still pass through."""
        from app.processor.jobs.brief_generation import _assemble_corpus

        driver = _FakeDriver(claims=[], briefs=[])
        _assemble_corpus(driver, "2026-05-10")
        # Robust to whitespace variations around the keyword.
        assert re.search(r"OPTIONAL\s+MATCH", driver.session_obj.last_cypher or "")


# ---------------------------------------------------------------------------
# End-to-end corpus assembly with mixed claims
# ---------------------------------------------------------------------------


@pytest.fixture
def mixed_claims() -> list[FakeClaim]:
    return [
        FakeClaim(
            claim_id="c-orphan",
            text="ORPHAN claim with no linked artifact",
            has_artifact=False,
        ),
        FakeClaim(
            claim_id="c-user",
            text="USER claim from a hand-authored note",
            source_type="manual",
        ),
        FakeClaim(
            claim_id="c-synth",
            text="SYNTH claim that came from a Cerid-written note",
            source_type="cerid-synthesis",
            cerid_reanalyze=False,
        ),
        FakeClaim(
            claim_id="c-synth-reanalyze",
            text="REANALYZE claim opted back into synthesis input",
            source_type="cerid-synthesis",
            cerid_reanalyze=True,
        ),
    ]


class TestBriefGenerationCorpusFilter:
    def test_cerid_synthesis_claim_excluded_by_default(self, mixed_claims):
        from app.processor.jobs.brief_generation import _assemble_corpus

        driver = _FakeDriver(claims=mixed_claims, briefs=[])
        _, notes_text, _ = _assemble_corpus(driver, "2026-05-10")
        assert "ORPHAN claim" in notes_text
        assert "USER claim" in notes_text
        assert "SYNTH claim" not in notes_text  # excluded
        assert "REANALYZE claim" in notes_text  # opted back in

    def test_orphan_claims_pass_through(self, mixed_claims):
        from app.processor.jobs.brief_generation import _assemble_corpus

        driver = _FakeDriver(claims=mixed_claims, briefs=[])
        _, notes_text, _ = _assemble_corpus(driver, "2026-05-10")
        assert "ORPHAN claim" in notes_text


class TestWeeklySynthesisCorpusFilter:
    def test_cerid_synthesis_claim_excluded_by_default(self, mixed_claims):
        from app.processor.jobs.weekly_synthesis import _build_vault_snapshot

        driver = _FakeDriver(claims=mixed_claims, briefs=[])
        snapshot = _build_vault_snapshot(driver, "2026-05-11")
        assert "ORPHAN claim" in snapshot
        assert "USER claim" in snapshot
        assert "SYNTH claim" not in snapshot
        assert "REANALYZE claim" in snapshot

    def test_brief_rows_still_included(self):
        """The loop-breaker is on :Claim only; :Brief rows still flow."""
        from app.processor.jobs.weekly_synthesis import _build_vault_snapshot

        briefs = [
            {"brief_id": "br-1", "kind": "daily", "sections": "Brief content"},
        ]
        driver = _FakeDriver(claims=[], briefs=briefs)
        snapshot = _build_vault_snapshot(driver, "2026-05-11")
        assert "DAILY" in snapshot
        assert "Brief content" in snapshot
