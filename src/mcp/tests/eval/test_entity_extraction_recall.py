"""Opt-in live check: recall against the annotated fixtures (CERID_LIVE_EXTRACTION_EVAL=1)."""

import json
import os

import pytest

from tests.eval.entity_recall_runner import FIXTURES, RECALL_FLOOR, annotation_files, run_one

pytestmark = pytest.mark.skipif(
    not os.getenv("CERID_LIVE_EXTRACTION_EVAL"), reason="live gateway eval",
)


@pytest.mark.parametrize("annot", annotation_files(), ids=lambda p: p.stem)
async def test_recall_against_annotations(annot):
    fixture, recall, forbidden, names = await run_one(annot)
    assert recall >= RECALL_FLOOR, (fixture, recall, sorted(names))
    assert not forbidden, (fixture, forbidden)


def test_every_expected_name_occurs_in_its_fixture():
    for annot in annotation_files():
        spec = json.loads(annot.read_text())
        text = (FIXTURES / spec["fixture"]).read_text().lower()
        missing = [n for n in spec["expected"] if n.lower() not in text]
        assert not missing, (annot.name, missing)
        assert len(spec["expected"]) >= 2, annot.name
