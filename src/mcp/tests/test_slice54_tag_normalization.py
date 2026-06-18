# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Slice 5.4 — tag vocabulary normalization in ai_categorize."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


def test_vocab_tag_kept_verbatim():
    from utils.metadata import _normalize_tags

    out = _normalize_tags(["invoice"], "finance")  # 'invoice' is in finance vocab
    assert "invoice" in out


def test_near_miss_maps_to_canonical_vocab():
    from utils.metadata import _normalize_tags

    # 'invoces' is an edit-distance near-miss of the 'invoice' vocab entry.
    out = _normalize_tags(["invoces"], "finance")
    assert "invoice" in out
    assert "invoces" not in out


def test_freeform_tags_capped():
    from utils.metadata import _normalize_tags

    tags = ["zzz-one", "zzz-two", "zzz-three", "zzz-four", "zzz-five"]
    out = _normalize_tags(tags, "finance", max_freeform=3)
    freeform = [t for t in out if t.startswith("zzz-")]
    assert len(freeform) == 3


def test_needs_review_always_preserved_and_uncapped():
    from utils.metadata import _normalize_tags

    tags = ["zzz-1", "zzz-2", "zzz-3", "needs-review"]
    out = _normalize_tags(tags, "finance", max_freeform=2)
    assert "needs-review" in out  # control tag survives the free-form cap


def test_canonicalizes_case_and_spaces():
    from utils.metadata import _normalize_tags

    out = _normalize_tags(["Quarterly Report"], "finance")
    assert "quarterly-report" in out


def test_dedupes_variant_and_canonical():
    from utils.metadata import _normalize_tags

    # Both the canonical and a near-miss present → single canonical entry.
    out = _normalize_tags(["invoice", "invoces"], "finance")
    assert out.count("invoice") == 1


def test_total_capped_at_ten():
    from utils.metadata import _normalize_tags

    tags = [f"tag-{i}" for i in range(20)]
    out = _normalize_tags(tags, "finance", max_freeform=20)
    assert len(out) <= 10


def _llm_json(payload: dict) -> str:
    return json.dumps(payload)


@pytest.mark.asyncio
async def test_ai_categorize_normalizes_tags_end_to_end():
    import config
    from utils import metadata

    payload = {
        "domain": "finance", "sub_category": "tax", "confidence": 0.9,
        "tags": ["invoces", "Quarterly Report", "zzz-extra"],  # near-miss + case + freeform
        "keywords": [], "summary": "s",
    }
    with (
        patch.object(config, "INTERNAL_LLM_PROVIDER", "openrouter"),
        patch("core.utils.llm_client.call_llm", new_callable=AsyncMock, return_value=_llm_json(payload)),
    ):
        out = await metadata.ai_categorize("an invoice", "inv.pdf", mode="pro")

    assert "invoice" in out["tags"]          # near-miss mapped to vocab
    assert "quarterly-report" in out["tags"]  # canonicalized
    assert "invoces" not in out["tags"]
