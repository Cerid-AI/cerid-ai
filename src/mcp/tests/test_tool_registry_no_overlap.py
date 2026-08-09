"""The two tool registries must stay disjoint, because nothing de-duplicates them.

`app/tools.py` composes the palette from two sources — the static `MCP_TOOLS`
list and the `@register_tool` decorator registry — and its header comment
claimed for a long time that `get_all_tools()` "returns the union de-duplicated
by name". It does not: it concatenates with `*` splats. The no-overlap property
the design depends on was therefore an unenforced convention that a comment
asserted and no code checked.

This is the check that makes the claim true. It fails the moment a name is added
to both registries, which is the only way the concatenation can go wrong.
"""

from __future__ import annotations

import collections

import pytest

pytest.importorskip("app.tools")

from app.tool_registry import get_registered_schemas  # noqa: E402
from app.tools import MCP_TOOLS, get_all_tools  # noqa: E402


def _names(schemas) -> list[str]:
    return [s["name"] for s in schemas if isinstance(s, dict) and "name" in s]


def test_static_and_decorator_registries_are_disjoint():
    legacy = set(_names(MCP_TOOLS))
    registered = set(_names(get_registered_schemas()))
    overlap = legacy & registered
    assert not overlap, (
        f"{len(overlap)} tool name(s) in BOTH registries: {sorted(overlap)}. "
        "get_all_tools() concatenates without de-duplicating, so each would be "
        "advertised twice; execute_tool would silently prefer the decorator one."
    )


def test_composed_palette_has_no_duplicate_names():
    """The property that actually matters, asserted on the composed result."""
    names = _names(get_all_tools())
    dupes = [n for n, c in collections.Counter(names).items() if c > 1]
    assert not dupes, f"duplicate tool names in the composed palette: {dupes}"


def test_legacy_list_length_matches_the_documented_count():
    """The header comment says 23; drift here is how it reached 28-vs-23."""
    assert len(MCP_TOOLS) == 23, (
        f"MCP_TOOLS holds {len(MCP_TOOLS)} entries; app/tools.py's header comment "
        "says 23. Update both together."
    )
