# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Drift gate for docs/TIER_MATRIX.md.

The published feature/tier matrix is generated from config.features by
scripts/gen_tier_matrix.py. This test shells out to the generator's --check
mode so the doc can never drift from the flag source of truth — adding or
re-tiering a flag forces a regenerate. Runs in the deps-complete ``test`` CI
job (the generator imports config.features), mirroring lint-pro-gating.py +
test_pro_gating_contract.py.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GEN_SCRIPT = REPO_ROOT / "scripts" / "gen_tier_matrix.py"


def test_tier_matrix_is_in_sync() -> None:
    if not GEN_SCRIPT.exists():
        pytest.skip(f"generator not present: {GEN_SCRIPT}")
    result = subprocess.run(
        [sys.executable, str(GEN_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            "docs/TIER_MATRIX.md is out of date — regenerate with "
            f"`python scripts/gen_tier_matrix.py`:\n{result.stdout}\n{result.stderr}"
        )


def test_every_pro_flag_appears_in_matrix() -> None:
    """Sanity: each Pro flag is rendered as a row (its gate name in backticks)."""
    sys.path.insert(0, str(REPO_ROOT / "src" / "mcp"))
    from config.features import _PRO_TIER_FLAGS

    matrix = (REPO_ROOT / "docs" / "TIER_MATRIX.md").read_text(encoding="utf-8")
    missing = [f for f in _PRO_TIER_FLAGS if f"`{f}`" not in matrix]
    assert not missing, f"Pro flags absent from TIER_MATRIX.md: {sorted(missing)}"
