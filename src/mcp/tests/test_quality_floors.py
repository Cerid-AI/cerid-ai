# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for per-source quality-floor drop decision (Phase 0.5 #10).

The wiring (ingest_content calls should_drop before staging chunks) is
exercised live; these cover the decision logic without a Neo4j round-trip
by seeding the floor cache.
"""
from __future__ import annotations

import pytest

from app.services import quality_floors


@pytest.fixture(autouse=True)
def _clear_cache():
    quality_floors.invalidate_cache()
    yield
    quality_floors.invalidate_cache()


def test_no_source_never_drops():
    assert quality_floors.should_drop(None, 0.0) is False


def test_unset_floor_never_drops():
    # source present in cache with floor 0.0 → never drops
    quality_floors._CACHE["s1"] = 0.0
    assert quality_floors.should_drop("s1", 0.01) is False


def test_score_below_floor_drops():
    quality_floors._CACHE["s1"] = 0.5
    assert quality_floors.should_drop("s1", 0.49) is True


def test_score_at_or_above_floor_keeps():
    quality_floors._CACHE["s1"] = 0.5
    assert quality_floors.should_drop("s1", 0.5) is False
    assert quality_floors.should_drop("s1", 0.9) is False


def test_invalidate_cache_clears_entry():
    quality_floors._CACHE["s1"] = 0.5
    quality_floors.invalidate_cache("s1")
    assert "s1" not in quality_floors._CACHE
