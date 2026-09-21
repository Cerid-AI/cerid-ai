# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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

import config.settings as settings_mod
from app.tool_registry import get_registered_schemas
from app.tools import _TRADING_TOOLS, MCP_TOOLS, get_all_tools

# The shipped tool palette, pinned by name. README.md, docs/API_REFERENCE.md and
# docs/ARCHITECTURE.md all advertise this list's length as the tool count; the
# optional trading module adds ``_TRADING_TOOLS`` on top when the flag is on.
#
# Pinning names rather than a floor is deliberate. A ">= N" gate cannot see a
# tool going missing while another is added, and the old floor was looser than
# the advertised count, so up to four tools could vanish silently. Regenerate
# with:
#   python -c "from app.tools import get_all_tools; \
#              print(sorted(t['name'] for t in get_all_tools()))"
CORE_TOOL_NAMES = frozenset({
    "pkb_agent_query",
    "pkb_answer_with_citations",
    "pkb_artifact_delete",
    "pkb_artifact_get",
    "pkb_artifacts",
    "pkb_audit",
    "pkb_batch",
    "pkb_check_hallucinations",
    "pkb_collections",
    "pkb_compare_artifacts",
    "pkb_concept_evolution",
    "pkb_correct",
    "pkb_curate",
    "pkb_digest",
    "pkb_endorse",
    "pkb_external_servers",
    "pkb_extract_claims",
    "pkb_extract_entities",
    "pkb_flag",
    "pkb_graph_communities",
    "pkb_graph_neighbors",
    "pkb_graph_path",
    "pkb_health",
    "pkb_hypothetical_doc",
    "pkb_inbox_filter",
    "pkb_inbox_triage",
    "pkb_ingest",
    "pkb_ingest_file",
    "pkb_ingest_multimodal",
    "pkb_ingest_url",
    "pkb_knowledge_pack_install",
    "pkb_knowledge_pack_list",
    "pkb_knowledge_pack_uninstall",
    "pkb_maintain",
    "pkb_memory_archive",
    "pkb_memory_extract",
    "pkb_memory_recall",
    "pkb_privacy_audit",
    "pkb_quarantine",
    "pkb_question_decompose",
    "pkb_rate",
    "pkb_recategorize",
    "pkb_recategorize_bulk",
    "pkb_rectify",
    "pkb_revisit_due",
    "pkb_scheduler_status",
    "pkb_search_filtered",
    "pkb_summarize_artifact",
    "pkb_summarize_domain",
    "pkb_surface_route",
    "pkb_timeline",
    "pkb_trending",
    "pkb_triage",
    "pkb_web_search",
    "pkb_wiki_lookup",
})


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


def test_tool_inventory_matches_the_shipped_palette() -> None:
    """The declared palette is the contract, not a floor.

    ``get_registered_schemas() + MCP_TOOLS`` is what the build itself declares;
    external MCP servers and tool plugins add to ``get_all_tools()`` at runtime
    and are deliberately excluded here so a developer's local plugin cannot make
    the gate pass or fail.

    Floor history (why this stopped being a ">= N" check):
    * v0.93.10 — 29 tools (pre-overhaul)
    * v0.95.0  — 56 tools (overhaul; pkb_query deprecated)
    * v0.96.0  — 55 tools, +5 with the optional trading module
    * v1.0.2   — floor replaced by an exact name set: the public floor had been
                 51 against 55 shipped tools, so four could disappear unnoticed.
    """
    declared = {t["name"] for t in (*get_registered_schemas(), *MCP_TOOLS)}
    missing = CORE_TOOL_NAMES - declared
    added = declared - CORE_TOOL_NAMES
    assert not missing, (
        f"tool inventory regressed: {len(missing)} tool(s) no longer register: "
        f"{sorted(missing)}"
    )
    assert not added, (
        f"{len(added)} tool(s) ship without being in the advertised palette: "
        f"{sorted(added)} — add them here and update the count in README.md, "
        f"docs/API_REFERENCE.md and docs/ARCHITECTURE.md"
    )


@pytest.mark.skipif(
    not _TRADING_TOOLS,
    reason="the optional trading module is not part of this distribution",
)
def test_trading_module_extends_the_palette_only_when_enabled(monkeypatch) -> None:
    """The trading delta is whatever the module exports — never a literal."""
    monkeypatch.setattr(settings_mod, "CERID_TRADING_ENABLED", False, raising=False)
    without = {t["name"] for t in get_all_tools()}
    monkeypatch.setattr(settings_mod, "CERID_TRADING_ENABLED", True, raising=False)
    with_trading = {t["name"] for t in get_all_tools()}

    trading_names = {t["name"] for t in _TRADING_TOOLS}
    assert with_trading - without == trading_names
    assert not (without & trading_names), (
        f"trading tools leak into the palette with the flag off: "
        f"{sorted(without & trading_names)}"
    )
