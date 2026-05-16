# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""MCP tool schema fidelity gate — prevents the pkb_artifacts bug class.

What this gate enforces, applied to every tool surfaced by
``app.tools.get_all_tools()``:

1. ``inputSchema`` and ``outputSchema`` exist.
2. ``inputSchema.type == "object"`` (per MCP 2024-11-05+).
3. ``outputSchema.type == "object"`` (per MCP 2024-11-05+ — this is the
   one that bit us on 2026-05-15 with ``pkb_artifacts`` declaring
   ``"array"``).
4. Both schemas are syntactically valid JSON Schema (jsonschema lib
   accepts them).
5. Required properties referenced in ``inputSchema.required`` actually
   appear in ``inputSchema.properties``.

Round-trip handler validation (mock handler → assert return matches
``outputSchema``) is *not* enforced here because it requires mocking
every handler's upstream deps (neo4j / chroma / redis / LLM). That
coverage lives in each tool's dedicated unit test instead. The
shape-level gate alone catches the strict-validation regressions that
caused Claude Code's MCP loader to drop the entire cerid-kb tool list.
"""
from __future__ import annotations

import pytest

from app.tools import get_all_tools


def _tool_ids():
    """Per-tool test parametrisation that keeps the failure messages
    readable (Pytest shows the tool name in the failure header)."""
    return [t["name"] for t in get_all_tools()]


@pytest.mark.parametrize("tool", get_all_tools(), ids=_tool_ids())
class TestEachToolSchema:
    """One class per tool — each test below runs against every tool."""

    def test_has_input_schema(self, tool: dict) -> None:
        assert "inputSchema" in tool, (
            f"{tool['name']}: missing inputSchema"
        )

    def test_has_output_schema(self, tool: dict) -> None:
        assert "outputSchema" in tool, (
            f"{tool['name']}: missing outputSchema. Every MCP tool ships "
            "one — wrap the return shape in an object envelope."
        )

    def test_input_schema_type_object(self, tool: dict) -> None:
        s = tool["inputSchema"]
        assert s.get("type") == "object", (
            f"{tool['name']}: inputSchema.type must be 'object' "
            f"(got {s.get('type')!r})"
        )

    def test_output_schema_type_object(self, tool: dict) -> None:
        """The bug that motivated this gate.

        MCP 2024-11-05+ requires ``outputSchema.type == 'object'`` because
        the schema describes the optional ``structuredContent`` envelope,
        which the spec mandates to be a JSON object. Pre-2026-05-15
        ``pkb_artifacts`` declared ``'array'`` and Claude Code's strict
        loader silently failed the entire cerid-kb registration.
        """
        s = tool["outputSchema"]
        assert s.get("type") == "object", (
            f"{tool['name']}: outputSchema.type must be 'object' "
            f"(got {s.get('type')!r}). Wrap arrays/strings in an object "
            "envelope: {'type': 'object', 'properties': {'items': "
            "{'type': 'array', ...}}}."
        )

    def test_required_fields_exist_in_properties(self, tool: dict) -> None:
        s = tool["inputSchema"]
        required = s.get("required", [])
        properties = s.get("properties", {})
        missing = [r for r in required if r not in properties]
        assert not missing, (
            f"{tool['name']}: inputSchema.required references undeclared "
            f"properties: {missing!r}"
        )

    def test_schemas_are_valid_json_schema(self, tool: dict) -> None:
        """Both schemas must be parseable as JSON Schema (any draft)."""
        try:
            import jsonschema  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("jsonschema not available")

        try:
            jsonschema.Draft7Validator.check_schema(tool["inputSchema"])
        except jsonschema.SchemaError as e:
            pytest.fail(
                f"{tool['name']}: inputSchema is not valid JSON Schema: {e.message}"
            )
        try:
            jsonschema.Draft7Validator.check_schema(tool["outputSchema"])
        except jsonschema.SchemaError as e:
            pytest.fail(
                f"{tool['name']}: outputSchema is not valid JSON Schema: {e.message}"
            )


def test_no_duplicate_tool_names() -> None:
    """Every tool name must be unique across the union of registered +
    legacy + external sources. A duplicate masks one tool's handler."""
    names = [t["name"] for t in get_all_tools()]
    duplicates = [n for n in set(names) if names.count(n) > 1]
    assert not duplicates, f"duplicate tool names: {duplicates!r}"


def test_tool_inventory_meets_minimum() -> None:
    """Sanity check: cerid-kb ships at least the 56 tools that were
    registered at v0.95.0.

    Floor history:
    * v0.93.10 — 29 tools (pre-overhaul)
    * v0.95.0 — 56 tools (overhaul)
    * v0.96.0 — will drop pkb_query → 55 floor (deprecation maturity)

    Bump this each release so a silent regression in TOOL_REGISTRY
    population or MCP_TOOLS truncation lands hard in CI.
    """
    names = [t["name"] for t in get_all_tools()]
    assert len(names) >= 56, (
        f"tool inventory regressed: only {len(names)} tools registered "
        f"(want >= 56)"
    )
