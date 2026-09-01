#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Generate .env.example from the os.getenv calls across src/mcp.

Walks the AST and emits a sorted .env.example with each discovered env var.

Two corrections, both made 2026-08-10 after `--check` reported "in sync"
while the entire Pro connector stack was undocumented:

1. It scanned ONLY settings.py, so anything read elsewhere was invisible —
   CERID_TIER (config/features.py), USER_GOOGLE_EMAIL (routers/connectors.py),
   CERID_TRIAL_DAYS and CERID_LICENSE_* (routers/license.py, utils/license.py),
   HF_TOKEN, and the connector-stack ports. The gate passed because it was
   comparing the file against a question it had narrowed to nothing.

2. A var whose default is COMPUTED rather than a literal was rendered
   `NAME=`, which is not "unset" — it sets the empty string, so the computed
   default in code never runs. `cp .env.example .env` produced an empty
   CERID_MACHINE_ID and a broken CERID_SIDECAR_URL. Those are now emitted
   commented out, so copying the file leaves the code's default intact.

Usage:
    python scripts/gen_env_example.py                 # regenerate, write to .env.example
    python scripts/gen_env_example.py --check         # exit 1 if the file is out of sync (CI mode)
"""
from __future__ import annotations

import argparse
import ast
import difflib
import sys
from pathlib import Path

# Sentinel: os.getenv had a default, but it is computed rather than literal.
COMPUTED_DEFAULT = "<computed>"

REPO_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_FILE = REPO_ROOT / "src" / "mcp" / "config" / "settings.py"
ENV_EXAMPLE_FILE = REPO_ROOT / ".env.example"
SCAN_ROOT = REPO_ROOT / "src" / "mcp"

# settings.py is the canonical config module: everything it reads is
# documented, whatever the name. For the OTHER modules now scanned, filter by
# RULE rather than a list nobody maintains — our own namespace plus the
# third-party credentials an operator genuinely has to supply — so widening the
# walk cannot sweep in one-off reads from unrelated code.
_PROJECT_PREFIX = "CERID_"
_THIRD_PARTY_ALLOWLIST = frozenset({
    # The two variables that ACTUALLY gate error reporting. Neither starts with
    # the project prefix, so both were invisible to this generator — meanwhile
    # ENABLE_SENTRY, which is in the internal-flag allowlist below, was the one
    # Sentry name an operator could find in .env.example, and it was inert.
    # A knob that does nothing, documented; three that do, hidden.
    "SENTRY_DSN_MCP",
    "SENTRY_DSN",
    "HF_TOKEN",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "NEO4J_PASSWORD",
    "NEO4J_USER",
    "NEO4J_URI",
    "USER_GOOGLE_EMAIL",
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "MS365_MCP_TENANT_ID",
})
# Retrieval feature flags read directly at each call site (hype_indexer.py,
# query_agent.py, core/retrieval/sparse.py) rather than centralized as
# settings.py attributes — settings.py:168 documents this as an intentional
# mirror of the parent-child pattern. AF-046: they were getenv-only and
# invisible to the AST walk, so list them explicitly like the third-party
# credentials above.
_INTERNAL_FLAG_ALLOWLIST = frozenset({
    "RETRIEVAL_HYPE_ENABLED",
    "RETRIEVAL_SPARSE_ENABLED",
})


def _is_documented_var(name: str) -> bool:
    return (
        name.startswith(_PROJECT_PREFIX)
        or name in _THIRD_PARTY_ALLOWLIST
        or name in _INTERNAL_FLAG_ALLOWLIST
    )


def _iter_sources():
    """Every non-test module under src/mcp, plus settings.py first."""
    yield SETTINGS_FILE
    for path in sorted(SCAN_ROOT.rglob("*.py")):
        if path == SETTINGS_FILE:
            continue
        parts = set(path.parts)
        if "tests" in parts or path.name.startswith("test_"):
            continue
        yield path


class GetenvVisitor(ast.NodeVisitor):
    """Collect (name, default) pairs from os.getenv(...) calls."""

    def __init__(self, *, filtered: bool = True) -> None:
        self.entries: list[tuple[str, str | None]] = []
        # settings.py is unfiltered; every other module is filtered by rule.
        self.filtered = filtered

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        # Match os.getenv("NAME", [default]) or os.environ.get("NAME", [default])
        if self._is_getenv_call(node) and node.args:
            name_node = node.args[0]
            if isinstance(name_node, ast.Constant) and isinstance(name_node.value, str):
                default = self._extract_default(node.args[1]) if len(node.args) > 1 else None
                if not self.filtered or _is_documented_var(name_node.value):
                    self.entries.append((name_node.value, default))
        self.generic_visit(node)

    @staticmethod
    def _is_getenv_call(node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os":
                return True
            # os.environ.get(...)
            if (
                func.attr == "get"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "environ"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "os"
            ):
                return True
        return False

    @staticmethod
    def _extract_default(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant):
            return str(node.value) if node.value is not None else None
        # A computed default (f-string, variable, call). Distinct from "no
        # default at all" — see COMPUTED_DEFAULT.
        return COMPUTED_DEFAULT


def extract_env_vars(sources: "list[tuple[str, str]] | str") -> list[tuple[str, str | None]]:
    """Accepts a single source string (legacy) or (label, source) pairs."""
    if isinstance(sources, str):
        sources = [("<source>", sources)]
    entries: list[tuple[str, str | None]] = []
    for label, src in sources:
        visitor = GetenvVisitor(filtered=(label != str(SETTINGS_FILE)))
        try:
            visitor.visit(ast.parse(src))
        except SyntaxError:
            continue
        entries.extend(visitor.entries)
    # Dedup: FIRST seen wins, and settings.py is yielded first, so the
    # canonical config module always decides. Preferring a literal from
    # elsewhere looked helpful but is wrong — CERID_SIDECAR_URL is computed
    # from CERID_SIDECAR_PORT in settings.py while inference_routing.py
    # hardcodes 8889 in three places, so the "helpful" literal would document a
    # value that goes stale the moment the port is changed.
    seen: dict[str, str | None] = {}
    for name, default in entries:
        if name not in seen:
            seen[name] = default
    return sorted(seen.items())


# Compose-interpolation vars: consumed by docker-compose.yml port/bind
# stanzas, never read by settings.py, so the AST walk cannot discover
# them. Registered explicitly (V1 Task 4.3 — INSTALL.md points operators
# here for port collisions). Keep in sync with docker-compose.yml.
COMPOSE_VARS: list[tuple[str, str]] = [
    # Connector stack (stacks/connectors/docker-compose.yml, --profile pro).
    # Absent until 2026-08-10: the whole Pro connector stack was undocumented
    # while the drift gate reported the file in sync.
    ("CERID_PORT_GOOGLE_MCP", "8810"),
    ("CERID_PORT_MS365_MCP", "8811"),
    ("CERID_BIND_ADDR", "127.0.0.1"),
    ("MS365_MCP_TENANT_ID", "common"),
    ("CERID_PORT_MCP", "8888"),
    ("CERID_PORT_GUI", "3000"),
    ("CERID_PORT_NEO4J", "7474"),
    ("CERID_PORT_NEO4J_BOLT", "7687"),
    ("CERID_PORT_CHROMA", "8001"),
    ("CERID_PORT_REDIS", "6379"),
    # Read by docker-compose.yml into the cerid-web container, never by
    # Python, so the AST walk cannot see it — yet RUNBOOK_PRODUCTION.md tells
    # operators to set it in THIS file. Absent until 2026-09-01.
    ("VITE_SENTRY_DSN_WEB", ""),
]


def render_env_example(entries: list[tuple[str, str | None]]) -> str:
    lines = [
        "# .env.example — auto-generated from src/mcp/config/settings.py",
        "# Generated by scripts/gen_env_example.py — do not edit by hand.",
        "# Regenerate with: python scripts/gen_env_example.py",
        "",
    ]
    for name, default in entries:
        if default is COMPUTED_DEFAULT:
            # Commented ON PURPOSE. `NAME=` would set the empty string and
            # defeat the default computed in code.
            lines.append(f"# {name}=   # default is computed at runtime")
        elif default is not None:
            lines.append(f"{name}={default}")
        else:
            lines.append(f"{name}=")
    lines += [
        "",
        "# Host port overrides — consumed by docker-compose.yml, not the app.",
        "# Change these when a default port collides with another service.",
    ]
    for name, default in sorted(COMPOSE_VARS):
        lines.append(f"{name}={default}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the tracked .env.example is out of sync with settings.py (CI mode)",
    )
    args = parser.parse_args(argv)

    sources = [(str(p), p.read_text(encoding="utf-8")) for p in _iter_sources()]
    entries = extract_env_vars(sources)
    expected = render_env_example(entries)

    if args.check:
        if not ENV_EXAMPLE_FILE.exists():
            print(".env.example missing — run: python scripts/gen_env_example.py", file=sys.stderr)
            return 1
        actual = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
        if actual != expected:
            print(
                ".env.example is out of sync with settings.py. "
                "Regenerate with: python scripts/gen_env_example.py",
                file=sys.stderr,
            )
            diff = list(
                difflib.unified_diff(
                    actual.splitlines(),
                    expected.splitlines(),
                    fromfile=".env.example (tracked)",
                    tofile=".env.example (generated)",
                    lineterm="",
                )
            )
            for line in diff[:40]:
                print(line, file=sys.stderr)
            return 1
        print(f".env.example is in sync ({len(entries)} env vars).")
        return 0

    ENV_EXAMPLE_FILE.write_text(expected)
    print(f"Wrote {len(entries)} env vars to {ENV_EXAMPLE_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
