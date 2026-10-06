# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The Private Mode level contract has one source and every copy agrees with it.

``src/web/src/lib/private-mode-levels.json`` is the source: the settings page,
the settings registry, the chat toolbar and the capability text all render from
it. ``docs/PRIVATE_MODE.md`` repeats it as a table for readers, and the server
names the same ladder in ``PrivateModeRequest`` and its threshold constants.
This test fails when any of them drifts.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.routers import settings as settings_router
from app.routers.settings import PrivateModeRequest
from app.services.private_mode import SKIP_SAVES_LEVEL
from core.agents.request_context import PRIVATE_MODE_SKIP_KB_LEVEL

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_JSON = REPO_ROOT / "src" / "web" / "src" / "lib" / "private-mode-levels.json"
CONTRACT_DOC = REPO_ROOT / "docs" / "PRIVATE_MODE.md"
TOOLS_PY = REPO_ROOT / "src" / "mcp" / "app" / "tools.py"

COLUMNS = ("retrieval", "memory", "server", "browser", "audit", "egress")


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT_JSON.read_text(encoding="utf-8"))


def _doc_table_rows() -> dict[int, dict[str, str]]:
    """Rows of the ``## Levels`` table, keyed by level, cells keyed by column."""
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    section = text.split("## Levels", 1)[1].split("\n## ", 1)[0]
    lines = [ln for ln in section.splitlines() if ln.startswith("|")]
    header = [c.strip().lower() for c in lines[0].strip("|").split("|")]
    rows: dict[int, dict[str, str]] = {}
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        row = dict(zip(header, cells, strict=True))
        rows[int(row["level"])] = row
    return rows


def test_levels_are_contiguous_and_match_the_request_validator(contract):
    levels = [entry["level"] for entry in contract["levels"]]
    assert levels == list(range(len(levels)))

    field = PrivateModeRequest.model_fields["level"]
    bounds = {type(m).__name__: m for m in field.metadata}
    assert bounds["Ge"].ge == levels[0]
    assert bounds["Le"].le == levels[-1]

    named = dict(re.findall(r"(\d)=([a-zA-Z ]+?)(?:,|\)|$)", field.description or ""))
    assert {int(k): v for k, v in named.items()} == {
        entry["level"]: entry["short"] for entry in contract["levels"]
    }


def test_thresholds_match_the_contract(contract):
    by_short = {entry["short"]: entry["level"] for entry in contract["levels"]}
    assert SKIP_SAVES_LEVEL == by_short["skip saves"]
    assert PRIVATE_MODE_SKIP_KB_LEVEL == by_short["skip KB"]
    assert settings_router._PRIVATE_MODE_L4 == by_short["full ephemeral"]
    tools_src = TOOLS_PY.read_text(encoding="utf-8")
    assert f"if not private_blocks({by_short['skip audit']}):" in tools_src
    assert '"mcp.tool_call"' in tools_src.split(f"private_blocks({by_short['skip audit']})", 1)[1][:400]


def test_doc_table_repeats_the_contract_cell_for_cell(contract):
    rows = _doc_table_rows()
    assert sorted(rows) == [entry["level"] for entry in contract["levels"]]
    for entry in contract["levels"]:
        row = rows[entry["level"]]
        assert row["name"] == entry["name"]
        for column in COLUMNS:
            assert row[column] == entry["withholds"][column], (entry["level"], column)


def test_no_level_claims_to_block_llm_egress(contract):
    assert "does not block" in contract["egress"].lower() or "no level blocks" in contract["egress"].lower()
    for entry in contract["levels"][1:]:
        assert entry["withholds"]["egress"].startswith("Not blocked"), entry["level"]
    doc = " ".join(CONTRACT_DOC.read_text(encoding="utf-8").split())
    assert contract["egress"] in doc


def test_each_level_states_every_dimension(contract):
    for entry in contract["levels"]:
        assert set(entry["withholds"]) == set(COLUMNS), entry["level"]
        assert entry["label"].startswith(f"L{entry['level']} — {entry['name']}")
        for column in COLUMNS:
            assert entry["withholds"][column].strip(), (entry["level"], column)
            assert "|" not in entry["withholds"][column]
