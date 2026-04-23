# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""AST-level contract guard for internal/external LLM helper call sites.

We've had two silent-regression incidents where a caller passed a kwarg
that the helper's signature didn't accept:

    * ``metadata.ai_categorize`` passed ``stage=`` to ``call_internal_llm``
      for weeks under a ``# type: ignore[call-arg]`` mask. In Ollama
      builds it raised ``TypeError`` inside a broad except, silently
      degrading every AI-categorized artifact to ``domain="general"``.
    * (historical) same class of mismatch around ``response_format``
      during the Bifrost retirement.

Type checkers don't catch it reliably (the ``# type: ignore`` comment
masks exactly the signal we want). A CI-visible runtime contract that
walks every call site in ``src/mcp/`` and asserts ``kwargs ⊆ signature
params`` makes the class un-droppable.

This test is **read-only**: it imports the helper module to read the
signature, parses the rest of the source tree with ``ast``, and
asserts on kwarg names only. No runtime calls, no mocks, no fixtures.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from ._helpers import repo_root

# Helpers under contract. Add entries to extend coverage to new hot-path
# helpers. Each value is the canonical import path used by callers
# (so the AST walker can recognize both ``from X import helper`` and
# ``X.helper(...)`` call shapes).
_HELPERS_UNDER_CONTRACT: dict[str, str] = {
    "call_internal_llm": "core.utils.internal_llm",
    "call_llm": "core.utils.llm_client",
}


def _src_dir() -> Path:
    root = repo_root()
    if root is None:
        # Fallback: walk up from this file to find src/mcp/ (works in the
        # ai-companion-mcp container where only /app is mounted).
        here = Path(__file__).resolve()
        for parent in here.parents:
            if parent.name == "src" or (parent / "core").exists():
                return parent
        pytest.skip("cannot locate src/mcp tree")
    return root / "src" / "mcp"


def _scan_roots() -> list[Path]:
    """Directories whose call sites are subject to this contract.

    Always includes ``src/mcp``. Also includes ``plugins/`` when running
    in a full checkout so first-party plugins (e.g. ``metamorphic``)
    can't quietly drop the ``stage=`` breadcrumb either.
    """
    roots = [_src_dir()]
    root = repo_root()
    if root is not None:
        plugins = root / "plugins"
        if plugins.exists():
            roots.append(plugins)
    return roots


def _allowed_kwargs(helper_name: str) -> set[str]:
    """Read the helper's current signature and return its accepted params."""
    mod_path = _HELPERS_UNDER_CONTRACT[helper_name]
    module = __import__(mod_path, fromlist=[helper_name])
    func = getattr(module, helper_name)
    return set(inspect.signature(func).parameters)


def _iter_call_kwargs(
    tree: ast.AST, helper_name: str
) -> list[tuple[int, set[str]]]:
    """Yield ``(lineno, {kwarg names})`` for each call to ``helper_name``.

    Accepts both bare-name ``helper(...)`` (local import) and
    attribute-access ``module.helper(...)`` forms.
    """
    out: list[tuple[int, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        is_match = (
            (isinstance(f, ast.Name) and f.id == helper_name)
            or (isinstance(f, ast.Attribute) and f.attr == helper_name)
        )
        if not is_match:
            continue
        kwargs = {kw.arg for kw in node.keywords if kw.arg is not None}
        out.append((node.lineno, kwargs))
    return out


def _py_files_under(root: Path) -> list[Path]:
    """Source files under ``root``, skipping caches, venvs, and tests."""
    skip_dirs = {
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "tests",  # tests may intentionally pass invalid kwargs to assert errors
        "venv", ".venv", "env",
    }
    results: list[Path] = []
    for p in root.rglob("*.py"):
        if any(part in skip_dirs for part in p.parts):
            continue
        results.append(p)
    return results


@pytest.mark.parametrize("helper", sorted(_HELPERS_UNDER_CONTRACT))
def test_all_call_sites_pass_allowed_kwargs(helper: str) -> None:
    """Every call site of ``helper`` uses only kwargs its signature accepts.

    Walks both ``src/mcp/`` and ``plugins/`` (when present) so first-party
    plugins are held to the same contract. Failure message includes
    file:line and the offending kwargs so the fix is one grep away.
    """
    allowed = _allowed_kwargs(helper)
    violations: list[str] = []

    for root in _scan_roots():
        for path in _py_files_under(root):
            try:
                tree = ast.parse(path.read_text(), filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for lineno, kwargs in _iter_call_kwargs(tree, helper):
                bad = kwargs - allowed
                if bad:
                    violations.append(f"{path}:{lineno}  invalid kwargs: {sorted(bad)}")

    assert not violations, (
        f"{helper}() called with kwargs outside its signature ({sorted(allowed)}):\n"
        + "\n".join(violations)
    )


def test_every_call_internal_llm_site_passes_stage() -> None:
    """Every ``call_internal_llm(...)`` call must include ``stage=``.

    Why: ``stage`` is the breadcrumb that flows into structlog + Sentry,
    and a missing tag silently degrades observability for one specific
    pipeline stage. The helper signature accepts ``stage`` as Optional
    so type checkers won't catch omission — this AST gate does.

    Scope: ``src/mcp/`` and ``plugins/``. Tests are intentionally
    skipped (they may construct calls dynamically). To opt out a
    specific call site (e.g. an experimental fixture), add the literal
    comment ``# stage-exempt`` on the same line as the call.
    """
    helper = "call_internal_llm"
    violations: list[str] = []

    for root in _scan_roots():
        for path in _py_files_under(root):
            try:
                source = path.read_text()
                tree = ast.parse(source, filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            source_lines = source.splitlines()
            for lineno, kwargs in _iter_call_kwargs(tree, helper):
                if "stage" in kwargs:
                    continue
                # Same-line opt-out token for rare exemptions.
                if 1 <= lineno <= len(source_lines) and "# stage-exempt" in source_lines[lineno - 1]:
                    continue
                violations.append(f"{path}:{lineno}  missing required kwarg: stage=")

    assert not violations, (
        "call_internal_llm() must always be called with stage=\"...\" "
        "so the breadcrumb flows to structlog + Sentry. Missing at:\n"
        + "\n".join(violations)
    )
