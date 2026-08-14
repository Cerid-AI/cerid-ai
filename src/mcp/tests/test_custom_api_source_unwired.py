# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``CustomApiSource`` must stay declared-dormant until a backend wires it.

``app/data_sources/custom.py`` ships a working, tested ``CustomApiSource``
(WP13's Custom API wizard backend), but no production code constructs one —
there is no endpoint that accepts a custom-source definition.

The frontend half USED to be a hidden button plus a restore-condition comment.
On 2026-08-14 (RA-26) the dialog component was deleted outright rather than
kept unmounted indefinitely, and the comment records that. Git history holds
the component if the endpoint is ever built.

This test is the interlock on that contract, in the
``test_usage_metering_wired`` style. It fails in both directions:

* someone lands a production construction site and leaves no frontend at all
  (feature exists, users cannot reach it)
* someone restores a Custom API surface without a backend (the 7-field form
  would silently discard again, which is why it was removed)
"""
from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent

# The frontend half of the contract. The marker sentence must match the
# comment in knowledge-console.tsx that documents why the button is hidden.
_CONSOLE = _SRC.parent / "web" / "src" / "components" / "kb" / "knowledge-console.tsx"
_DROPPED_MARKER = '"Add Custom API" was dropped on 2026-08-14 (RA-26)'
# The component itself must stay gone while the backend is dormant.
_DIALOG = _SRC.parent / "web" / "src" / "components" / "kb" / "custom-api-dialog.tsx"

# The definition itself is not a call site.
_EXEMPT = {_SRC / "app" / "data_sources" / "custom.py"}


def _production_construction_sites() -> set[str]:
    """Return ``path:line`` for every non-test ``CustomApiSource(...)`` call."""
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
            if name == "CustomApiSource":
                found.add(f"{path.relative_to(_SRC)}:{node.lineno}")
    return found


def test_custom_api_dormancy_matches_frontend():
    assert _CONSOLE.is_file(), (
        f"{_CONSOLE} not found — the interlock can no longer see the "
        "frontend half of the Custom API contract. Update the path here."
    )
    surface_dropped = _DROPPED_MARKER in _CONSOLE.read_text(encoding="utf-8")
    sites = _production_construction_sites()

    if sites:
        assert not surface_dropped, (
            f"CustomApiSource now has production construction sites "
            f"({sorted(sites)}) but knowledge-console.tsx still records the "
            "Custom API surface as dropped. Restore a surface in the same "
            "commit that landed the endpoint — the component is in git "
            "history at src/web/src/components/kb/custom-api-dialog.tsx."
        )
    else:
        assert surface_dropped, (
            "The RA-26 drop note is gone from knowledge-console.tsx but no "
            "production code constructs CustomApiSource — a restored surface "
            "would silently discard the form again. Land the endpoint first."
        )
        assert not _DIALOG.exists(), (
            "custom-api-dialog.tsx is back while CustomApiSource still has no "
            "production construction site. That is the unmounted-orphan state "
            "RA-26 removed; land the endpoint before restoring the component."
        )
