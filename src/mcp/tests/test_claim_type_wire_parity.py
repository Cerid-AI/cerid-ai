# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Claim-type vocabulary must agree across emitter, model, and frontend.

Three surfaces describe the same enum and all three had drifted by 2026-07-30:

* ``streaming._claim_type`` — the emitter, able to return ``recency``
* ``models.ClaimType``      — the canonical model, missing ``recency``
* ``types.ts BaseClaim``    — the frontend union, missing ``recency``

The divergence was silent because ``ClaimType`` had no importers outside tests:
the model was never fed real traffic, so its inability to represent a live wire
value cost nothing — right up until a consumer followed models.py's own
instruction to "read from the Pydantic model", at which point it would 422 on
ordinary recency claims.

These tests fail if any surface gains or loses a member without the others.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.agents.hallucination.models import ClaimType

_REPO = Path(__file__).resolve().parents[3]
_TYPES_TS = _REPO / "src" / "web" / "src" / "lib" / "types.ts"

# Every literal `_claim_type` can return, read off its branches in
# core/agents/hallucination/streaming.py.
_EMITTABLE = {"factual", "evasion", "ignorance", "citation", "recency"}


def test_model_covers_every_emittable_claim_type():
    """ClaimType must be a superset of what the pipeline can emit."""
    missing = _EMITTABLE - {c.value for c in ClaimType}
    assert not missing, (
        f"streaming._claim_type can emit {sorted(missing)} but ClaimType cannot "
        "represent it — any consumer honouring the canonical-model contract "
        "will reject real traffic."
    )


def test_emitter_still_returns_only_known_types():
    """Guard the other direction: a new branch must update the model."""
    src = (
        _REPO / "src" / "mcp" / "core" / "agents" / "hallucination" / "streaming.py"
    ).read_text(encoding="utf-8")
    body = src.split("def _claim_type(", 1)[1].split("\n    # Notify frontend", 1)[0]
    returned = set(re.findall(r'return "([a-z_]+)"', body))
    unknown = returned - {c.value for c in ClaimType}
    assert not unknown, (
        f"_claim_type gained return value(s) {sorted(unknown)} with no matching "
        "ClaimType member."
    )


@pytest.mark.skipif(not _TYPES_TS.exists(), reason="frontend not present")
def test_frontend_union_matches_the_model():
    """The TS union renders these values; a missing member is a type error."""
    src = _TYPES_TS.read_text(encoding="utf-8")
    match = re.search(r"claim_type\?:\s*([^\n]+)", src)
    assert match, "claim_type declaration not found in types.ts"
    ts_values = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    model_values = {c.value for c in ClaimType}
    assert ts_values == model_values, (
        f"frontend union {sorted(ts_values)} != ClaimType {sorted(model_values)}; "
        "the wire can carry a value the UI cannot type."
    )
