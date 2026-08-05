# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``/auth/me/usage`` must not claim to meter what nothing records.

``utils.usage`` ships working ``record_query`` / ``record_ingestion`` helpers
with unit tests, but as of the 2026-07-29 GA audit neither has a production call
site — so the endpoint returns a permanently-zero meter. That is only safe while
it is *declared* unwired.

This test is the interlock. It fails in both directions:

* someone wires the recorders and forgets to flip ``_USAGE_METERING_WIRED``
* someone flips the flag without wiring anything

Either way the endpoint's honesty claim and the code agree, or the build breaks.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent
_RECORDERS = {"record_query", "record_ingestion"}

# Modules that legitimately reference the recorders without being a call site.
_EXEMPT = {
    _SRC / "utils" / "usage.py",  # the definitions themselves
}


def _production_call_sites() -> set[str]:
    """Return ``path:line`` for every non-test call to a usage recorder."""
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        if "tests" in path.parts or path in _EXEMPT:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - syntax is enforced elsewhere
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else None
            )
            if name in _RECORDERS:
                found.add(f"{path.relative_to(_SRC)}:{node.lineno}")
    return found


def test_metering_flag_matches_reality():
    from app.routers.auth import _USAGE_METERING_WIRED

    sites = _production_call_sites()

    if _USAGE_METERING_WIRED:
        assert sites, (
            "_USAGE_METERING_WIRED is True but no production code calls "
            "record_query/record_ingestion — /auth/me/usage would report a "
            "fake meter as genuine."
        )
    else:
        assert not sites, (
            "usage recorders now have production call sites "
            f"({sorted(sites)}) — flip _USAGE_METERING_WIRED to True in "
            "app/routers/auth.py so /auth/me/usage stops disclaiming itself."
        )


def test_usage_endpoint_discloses_metering_state():
    """The response must always carry the ``metered`` disclosure."""
    import inspect

    from app.routers import auth as auth_router

    source = inspect.getsource(auth_router.user_usage)
    assert '"metered"' in source, (
        "/auth/me/usage dropped its metered disclosure; an unwired meter "
        "reading zero is indistinguishable from real zero consumption."
    )
