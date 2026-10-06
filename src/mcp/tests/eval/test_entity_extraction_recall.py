"""Fixture coverage + consistency run in plain pytest; the recall check against
the live extractor is opt-in (CERID_LIVE_EXTRACTION_EVAL=1)."""

import json
import os
import re

import pytest

from tests.eval.entity_recall_runner import (
    FIXTURES,
    RECALL_FLOOR,
    annotation_files,
    run_one,
    uncovered_fixtures,
)


def test_every_fixture_is_scored_or_excluded():
    missing = uncovered_fixtures()
    assert not missing, (
        f"fixtures with no annotation and no UNANNOTATED reason: {sorted(missing)}"
    )


@pytest.mark.skipif(not os.getenv("CERID_LIVE_EXTRACTION_EVAL"), reason="live gateway eval")
@pytest.mark.parametrize("annot", annotation_files(), ids=lambda p: p.stem)
async def test_recall_against_annotations(annot):
    result = await run_one(annot)
    assert result.best is not None and result.best >= RECALL_FLOOR, (
        result.fixture, result.attempts, sorted(result.names),
    )
    assert not result.forbidden, (result.fixture, result.forbidden)


def test_every_expected_name_occurs_in_its_fixture():
    for annot in annotation_files():
        spec = json.loads(annot.read_text())
        text = (FIXTURES / spec["fixture"]).read_text().lower()
        missing = [n for n in spec["expected"] if n.lower() not in text]
        assert not missing, (annot.name, missing)
        assert len(spec["expected"]) >= 2, annot.name


# A currency sign or a trailing currency word around digits: "$85,000",
# "85,000 USD", "85000 dollars".
_MONEY_RE = re.compile(r"^[$€£]\s?[\d.,]+$|^[\d.,]+\s?(?:dollars|usd|eur|gbp)$", re.IGNORECASE)


def test_every_expected_name_is_proper_noun_shaped():
    """Tripwire for the annotation contract: ``expected`` holds proper nouns
    per ``EntityType`` (names, titled works, dates, standards), never concepts
    or quantities. The proxy — first letter capitalised, or any digit, or any
    uppercase letter — is cheap and leaky: it accepts a sentence-initial common
    noun, a unit abbreviation ("25 Mbps") and a bare year, and it would reject
    a genuinely lower-case brand name, of which this corpus has none. It exists
    to catch the 2026-09 shape ("basil", "morning", "ad-blocking", "$85,000"),
    not to define the contract; the README in ``fixtures/`` does that.
    """
    for annot in annotation_files():
        spec = json.loads(annot.read_text())
        common = [
            n for n in spec["expected"]
            if not (n[:1].isupper() or any(ch.isdigit() for ch in n) or any(ch.isupper() for ch in n))
        ]
        money = [n for n in spec["expected"] if _MONEY_RE.match(n)]
        assert not common and not money, (annot.name, common, money)
