# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""`_to_record` must surface the persisted `quality_floor` so the source
detail UI can seed its slider from it. Without this, "Apply policy" silently
reset an existing non-zero floor to 0 (degrading retrieval)."""
from __future__ import annotations

from app.routers.sources import _to_record


def _node(**over: object) -> dict:
    base = {
        "id": "src-1",
        "kind": "rss_feed",
        "display_name": "Example feed",
    }
    base.update(over)
    return base


def test_quality_floor_passes_through() -> None:
    rec = _to_record(_node(quality_floor=0.42))
    assert rec.quality_floor == 0.42


def test_quality_floor_defaults_to_zero_when_absent() -> None:
    rec = _to_record(_node())
    assert rec.quality_floor == 0.0


def test_quality_floor_none_coerces_to_zero() -> None:
    rec = _to_record(_node(quality_floor=None))
    assert rec.quality_floor == 0.0
