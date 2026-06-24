# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from unittest.mock import MagicMock

from app.db.neo4j.wiki import get_confidence_band


def _driver_returning(trust_state):
    """Fake driver whose session.run().single() returns {trust_state: ...}."""
    row = None if trust_state is _MISSING else {"trust_state": trust_state}
    session = MagicMock()
    session.run.return_value.single.return_value = row
    cm = MagicMock()
    cm.__enter__.return_value = session
    driver = MagicMock()
    driver.session.return_value = cm
    return driver


_MISSING = object()


def test_verified_maps_to_high():
    assert get_confidence_band(_driver_returning("verified"), "e1") == "high"


def test_partial_maps_to_medium():
    assert get_confidence_band(_driver_returning("partial"), "e1") == "medium"


def test_unverified_maps_to_low():
    assert get_confidence_band(_driver_returning("unverified"), "e1") == "low"


def test_null_trust_state_is_unknown():
    assert get_confidence_band(_driver_returning(None), "e1") == "unknown"


def test_entity_absent_is_unknown():
    assert get_confidence_band(_driver_returning(_MISSING), "e1") == "unknown"
