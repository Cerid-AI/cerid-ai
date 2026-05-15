# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Registered MCP tool modules.

Each module in this package uses ``@register_tool`` from
``app.tool_registry`` to colocate one tool's schema + handler.
Importing this package triggers every module's import, which
populates ``TOOL_REGISTRY`` at app-startup.

Organisation by category (each maps to one ``v0.95`` overhaul phase):

* ``fundamentals``  — Phase 1.5 (artifact_get / _delete / search_filtered / recategorize_bulk)
* ``retrieval``     — Phase 3   (answer_with_citations, question_decompose, ...)
* ``graph``         — Phase 4   (graph_neighbors, graph_path, ...)
* ``feedback``      — Phase 5   (rate, correct, endorse, flag)
* ``temporal``      — Phase 6   (timeline, trending, revisit_due, ...)
* ``batch``         — Phase 7   (pkb_batch)

A future split into per-category MCP servers (``cerid-kb-graph``,
``cerid-kb-feedback``) lifts a module over without rewrite.
"""
from __future__ import annotations

# Import each module so its @register_tool decorators fire.
# Order doesn't matter for correctness; alphabetical for readability.
from app.mcp_tools import batch  # noqa: F401
from app.mcp_tools import feedback  # noqa: F401
from app.mcp_tools import fundamentals  # noqa: F401
from app.mcp_tools import graph_tools  # noqa: F401
from app.mcp_tools import retrieval  # noqa: F401
from app.mcp_tools import temporal  # noqa: F401
