# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Packaging guards: the shipped test-suite must run against the checkout.

Without a ``pythonpath`` entry the suite silently imports whatever ``cerid``
happens to be installed in the interpreter — a stale wheel, or another
checkout's editable install — so a green run proves nothing about the code
in this tree.
"""
from __future__ import annotations

from pathlib import Path

import cerid

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_imported_cerid_is_this_checkout() -> None:
    imported_src = Path(cerid.__file__).resolve().parents[1]
    assert imported_src == PACKAGE_ROOT / "src", (
        f"tests imported cerid from {imported_src}, not this checkout's "
        f"{PACKAGE_ROOT / 'src'} — pytest's pythonpath config is missing, so "
        "the suite is exercising an unrelated install"
    )
