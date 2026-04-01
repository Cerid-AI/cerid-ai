# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for _hydrate_settings_from_sync() startup behaviour."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def sync_dir(tmp_path: Path) -> Path:
    """Create a temporary sync directory with ``user/`` sub-directory."""
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    return tmp_path


def _write_settings(sync_dir: Path, settings: dict) -> None:
    path = sync_dir / "user" / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")


def _import_hydrate():
    """Import _hydrate_settings_from_sync inside the test to avoid import-time side effects."""
    from main import _hydrate_settings_from_sync
    return _hydrate_settings_from_sync


# ── Boolean toggles ─────────────────────────────────────────────────────────


def test_hydrates_boolean_toggles(sync_dir: Path) -> None:
    _write_settings(sync_dir, {"enable_feedback_loop": True})

    with patch("config.SYNC_DIR", str(sync_dir)):
        _import_hydrate()()

    import config
    assert config.ENABLE_FEEDBACK_LOOP is True


def test_skips_non_bool_toggle(sync_dir: Path) -> None:
    """Non-boolean values for toggle keys should be ignored."""
    import config
    original = config.ENABLE_FEEDBACK_LOOP

    _write_settings(sync_dir, {"enable_feedback_loop": "yes"})

    with patch("config.SYNC_DIR", str(sync_dir)):
        _import_hydrate()()

    assert config.ENABLE_FEEDBACK_LOOP == original


# ── Categorical + numeric ───────────────────────────────────────────────────


def test_hydrates_numeric_params(sync_dir: Path) -> None:
    _write_settings(sync_dir, {
        "cost_sensitivity": "high",
        "hallucination_threshold": 0.9,
    })

    with patch("config.SYNC_DIR", str(sync_dir)):
        _import_hydrate()()

    import config
    assert config.COST_SENSITIVITY == "high"
    assert config.HALLUCINATION_THRESHOLD == pytest.approx(0.9)


def test_rejects_out_of_range_numeric(sync_dir: Path) -> None:
    """Numeric values outside the allowed range should be ignored."""
    import config
    original = config.HALLUCINATION_THRESHOLD

    _write_settings(sync_dir, {"hallucination_threshold": 5.0})

    with patch("config.SYNC_DIR", str(sync_dir)):
        _import_hydrate()()

    assert config.HALLUCINATION_THRESHOLD == pytest.approx(original)


def test_rejects_invalid_categorical(sync_dir: Path) -> None:
    """Categorical values not in the allowed set should be ignored."""
    import config
    original = config.CATEGORIZE_MODE

    _write_settings(sync_dir, {"categorize_mode": "turbo"})

    with patch("config.SYNC_DIR", str(sync_dir)):
        _import_hydrate()()

    assert config.CATEGORIZE_MODE == original


# ── No-op paths ──────────────────────────────────────────────────────────────


def test_noop_when_no_sync_dir() -> None:
    """Empty SYNC_DIR should return without error."""
    with patch("config.SYNC_DIR", ""):
        _import_hydrate()()  # should not raise


def test_noop_when_no_settings_file(tmp_path: Path) -> None:
    """Valid sync_dir but no settings.json should return without error."""
    (tmp_path / "user").mkdir()
    with patch("config.SYNC_DIR", str(tmp_path)):
        _import_hydrate()()  # should not raise
