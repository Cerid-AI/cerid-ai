# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Mojibake detection/reversal + the entity repair plan (sf-3).

The observed defect: "Guardian Tigerâ€™s Eye…" stored as an entity name
after the mail connector's quoted-printable decode treated UTF-8 bytes
as code points. The repair must be exact for mojibake and a no-op for
legitimate accented text.
"""
from __future__ import annotations

import importlib.util

import pytest

from core.utils.mojibake import fix_mojibake, looks_like_mojibake

# The UTF-8 bytes of ’ (E2 80 99) decoded as latin-1 — raw C1 controls.
RAW_LATIN1 = "Guardian Tigerâs Eye"
# The same bytes through a cp1252 display — printable € ™ shapes.
CP1252_DISPLAY = "Guardian Tigerâ€™s Eye"
FIXED = "Guardian Tiger’s Eye"


class TestDetection:
    @pytest.mark.parametrize("text", [RAW_LATIN1, CP1252_DISPLAY, "CafÃ© de la Paix"])
    def test_flags_mojibake(self, text):
        assert looks_like_mojibake(text) is True

    @pytest.mark.parametrize("text", [
        "café",
        "Zürich Insurance",
        "plain ascii",
        "",
        FIXED,  # the repaired form must not re-flag
    ])
    def test_keeps_legitimate_text(self, text):
        assert looks_like_mojibake(text) is False


class TestRepair:
    def test_reverses_raw_latin1_shape(self):
        assert fix_mojibake(RAW_LATIN1) == FIXED

    def test_reverses_cp1252_display_shape(self):
        assert fix_mojibake(CP1252_DISPLAY) == FIXED

    def test_reverses_accented_letters(self):
        assert fix_mojibake("CafÃ© de la Paix") == "Café de la Paix"

    def test_legitimate_text_is_untouched(self):
        for text in ("café", "Zürich Insurance", "plain ascii", ""):
            assert fix_mojibake(text) == text


# ---------------------------------------------------------------------------
# Repair planning (scripts/repair_mojibake_entities.py, pure half)
# ---------------------------------------------------------------------------


def _load_script():
    from tests._helpers import repo_root

    root = repo_root()
    if root is None:
        return None
    script_path = root / "scripts" / "repair_mojibake_entities.py"
    if not script_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(
        "_cerid_test_script_repair_mojibake", script_path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def script():
    m = _load_script()
    if m is None:
        pytest.skip("scripts/repair_mojibake_entities.py not reachable")
    return m


class TestPlanRepairs:
    def test_mojibake_entity_gets_a_rename_plan(self, script):
        records = [{"props": {
            "canonical_id": "other:guardian-tiger-s-eye",
            "name": RAW_LATIN1,
            "entity_type": "OTHER",
        }}]
        plan = script.plan_repairs(records)
        assert len(plan) == 1
        entry = plan[0]
        assert entry["fixed_name"] == FIXED
        assert entry["action"] in ("rename", "rename_or_merge")

    def test_clean_entities_are_left_alone(self, script):
        records = [{"props": {
            "canonical_id": "org:zurich-insurance",
            "name": "Zürich Insurance",
            "entity_type": "ORG",
        }}]
        assert script.plan_repairs(records) == []
