#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lint MCP tool descriptions against the canonical style guide.

The style guide (docs/MCP_TOOL_STYLE.md) requires every tool's
``description`` field to contain three anchors so LLM clients can
plan tool chains reliably:

* an **action verb-phrase** (the first sentence)
* a ``**Use when**`` clause explaining the triggering situation
* a ``**Returns**`` clause sketching the result shape

Pre-v0.95 most descriptions were one-liners which forced LLMs to
guess between similarly-named tools (the
``pkb_query``/``pkb_agent_query`` confusion, the
``pkb_rectify``/``pkb_maintain``/``pkb_audit`` overlap). Phase 1.4
rewrote the 24 non-trading tools and Phase 1.6 made the registry
make ``description`` a first-class field. This linter prevents
regression on future additions.

Usage::

    # Local — fail-fast on any violation:
    python3 scripts/lint-mcp-descriptions.py

    # CI — warn-only mode (initial roll-out) prints findings then
    # exits 0 so PRs don't block while operators fix existing gaps:
    python3 scripts/lint-mcp-descriptions.py --warn-only

The linter is intentionally an external script (not a pytest test)
so it can be wired into CI as a separate warn-only job in v0.95.1
then promoted to blocking in v0.96 once the gaps are closed. The
pytest companion (``tests/test_mcp_tool_schema_fidelity.py``)
already enforces structural invariants; this lint enforces
*prose* quality.

Allowlist of names exempt from the linter (e.g. tools whose surface
is opaque-by-design or generated from external MCPs) lives in
``_ALLOWLIST`` below. Add a tool to the allowlist sparingly —
prefer fixing the description.
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow running outside docker by pointing at the same src/mcp Python path.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src", "mcp"))


# Tools exempt from the style-guide check. Keep this list short and
# justify each entry inline.
_ALLOWLIST: dict[str, str] = {
    # No entries currently — added here when a tool's description
    # legitimately cannot fit the schema (e.g. a tool whose entire
    # purpose is a single boolean ping).
}


def _violations(name: str, description: str) -> list[str]:
    """Return a list of style-guide violations on this description.

    Empty list = passes.
    """
    out: list[str] = []
    if not description or not description.strip():
        out.append("empty description")
        return out
    if len(description) < 40:
        out.append(f"description too terse ({len(description)} chars; want >=40)")
    if "**Use when**" not in description and "Use when" not in description:
        out.append("missing 'Use when' anchor")
    if "**Returns**" not in description and "Returns " not in description:
        out.append("missing 'Returns' anchor")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP tool description linter")
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Exit 0 even on violations (CI roll-out mode)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit findings as JSON (one object per offender)",
    )
    args = parser.parse_args()

    # Late import so the script can be invoked without the cerid stack
    # running; the registry populates from import-time decorators
    # against tools.py + mcp_tools/*.
    try:
        from app.tools import get_all_tools  # type: ignore[import-not-found]
    except ImportError as exc:
        print(f"ERROR: cannot import app.tools: {exc}", file=sys.stderr)
        return 2

    tools = get_all_tools()
    findings: list[dict] = []
    for t in tools:
        name = t.get("name", "<unnamed>")
        if name in _ALLOWLIST:
            continue
        violations = _violations(name, t.get("description", ""))
        if violations:
            findings.append({"name": name, "violations": violations})

    if args.json:
        import json
        print(json.dumps({"findings": findings, "total": len(findings)}, indent=2))
    else:
        if findings:
            print(f"MCP description lint: {len(findings)} tool(s) violate the style guide:\n")
            for f in findings:
                print(f"  {f['name']}:")
                for v in f["violations"]:
                    print(f"    - {v}")
            print(
                "\nFix per docs/MCP_TOOL_STYLE.md. Style: "
                "'{action}. **Use when** {trigger}. **Returns** {shape}. {caveats}'"
            )
        else:
            print(f"MCP description lint: all {len(tools)} tool(s) pass.")

    if findings and not args.warn_only:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
