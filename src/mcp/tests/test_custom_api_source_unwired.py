# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``CustomApiSource`` must stay declared-dormant until a backend wires it.

``app/data_sources/custom.py`` ships a working, tested ``CustomApiSource``
(WP13's Custom API wizard backend), but no production code constructs one —
there is no endpoint that accepts a custom-source definition. The frontend
half handles this honestly: ``knowledge-console.tsx`` hides the "Add Custom
API" button behind a comment that says to restore it *in the same commit that
lands the endpoint* (the prior wiring silently discarded the form).

This test is the interlock on that comment contract, in the
``test_usage_metering_wired`` style. It fails in both directions:

* someone lands a production construction site and forgets to restore the
  frontend button (feature exists, users can't reach it)
* someone restores the button without a backend (7-field form silently
  discarded again)
"""
from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent

# The frontend half of the contract. The marker sentence must match the
# comment in knowledge-console.tsx that documents why the button is hidden.
_CONSOLE = _SRC.parent / "web" / "src" / "components" / "kb" / "knowledge-console.tsx"
_HIDDEN_MARKER = '"Add Custom API" is hidden until a backend exists'

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
    button_hidden = _HIDDEN_MARKER in _CONSOLE.read_text(encoding="utf-8")
    sites = _production_construction_sites()

    if sites:
        assert not button_hidden, (
            f"CustomApiSource now has production construction sites "
            f"({sorted(sites)}) but knowledge-console.tsx still hides the "
            "'Add Custom API' button. Restore the button in the same commit "
            "that landed the endpoint — see the comment in the tsx."
        )
    else:
        assert button_hidden, (
            "The 'Add Custom API' hidden-button comment is gone from "
            "knowledge-console.tsx but no production code constructs "
            "CustomApiSource — a restored button would silently discard the "
            "form again. Land the endpoint first, or keep the button hidden."
        )
