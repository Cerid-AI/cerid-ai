# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract test: every frontend ``fetch`` URL must map to a real backend route.

Catches the class of bug where the frontend hits an endpoint that doesn't
exist (e.g. ``/admin/kb/duplicates`` 404 against the Knowledge "Duplicates"
button) or that was renamed without updating the caller.

Method:
1. Boot the FastAPI app and collect every registered route's path + methods.
2. Read every ``src/web/src/lib/api/*.ts`` file and extract every
   `` `${MCP_BASE}/...` `` template literal. Substitute path-param
   interpolations (``${encodeURIComponent(x)}`` etc.) with ``{x}``.
3. For each frontend URL + HTTP method pair, find a matching backend route
   that accepts that method on a path matching the same shape (path params
   compared structurally, not by name).

A failure lists the orphan URLs so the dev knows exactly which fetch needs
a backend route — or which backend route was renamed/removed without
updating the frontend.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
API_DIR = REPO_ROOT / "src" / "web" / "src" / "lib" / "api"

# Match `${MCP_BASE}/<path>` template literals plus the method passed to
# fetch (default GET when no second arg). The fetch may span multiple lines.
URL_RE = re.compile(r"`\$\{MCP_BASE\}(/[^`]*)`")
METHOD_RE = re.compile(r"method:\s*['\"](GET|POST|PUT|PATCH|DELETE)['\"]")
PARAM_INTERP_RE = re.compile(r"\$\{[^}]+\}")
QUERY_STRIP_RE = re.compile(r"\?.*$")
PARAM_PLACEHOLDER_RE = re.compile(r"\{[^/]+\}")

# Frontend URLs that legitimately have no exact backend route in this app —
# typically because the dynamic suffix is a closed set of action strings
# the backend exposes individually (``/automations/{id}/enable`` etc.). Keep
# small; entries here are normalised (METHOD, path) tuples.
ALLOWLIST: set[tuple[str, str]] = {
    # toggleAutomation builds /automations/{id}/{action} where action is one of
    # "enable" / "disable"; runAutomation hits /automations/{id}/run. Backend
    # exposes those three endpoints individually.
    ("POST", "/automations/{p}/{p}"),
}


def _build_route_set() -> set[tuple[str, str]]:
    """Collect every backend route by walking ``app/routers/*.py`` directly,
    instead of touching the shared FastAPI app — which would mutate
    process-wide state (env vars, ``config.features`` cache, sys.modules)
    and leak into subsequent tests in the same session.

    The auth router is only mounted on ``app.main`` when
    ``CERID_MULTI_USER=true``; we include it unconditionally here because
    "is this a real route in some deployment?" is what the contract cares
    about, not "is it active in the current test process?".
    """
    import importlib

    from fastapi import APIRouter

    out: set[tuple[str, str]] = set()
    routers_dir = Path(__file__).resolve().parents[1] / "app" / "routers"
    for py_file in sorted(routers_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        modname = f"app.routers.{py_file.stem}"
        try:
            mod = importlib.import_module(modname)
        except Exception:
            continue
        # A module can expose ``router`` and/or specialised auxiliaries
        # (e.g. agent_console.activity_router).
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if not isinstance(attr, APIRouter):
                continue
            for route in attr.routes:
                path = getattr(route, "path", None)
                methods = getattr(route, "methods", None) or set()
                if not path:
                    continue
                # FastAPI's APIRouter applies its prefix at decoration
                # time, so ``route.path`` already includes the router prefix.
                for method in methods:
                    if method == "HEAD":
                        continue
                    out.add((method.upper(), path))
    return out


def _collect_backend_routes() -> set[tuple[str, str]]:
    """Return the set of (METHOD, normalised-path) supported by the backend.

    Path placeholders are kept in their FastAPI ``{name}`` form here and
    are collapsed to ``{p}`` later via :func:`_normalise_backend_path`.
    """
    return _build_route_set()


_TRAILING_QS_INTERP_RE = re.compile(r"(?<!/)\$\{[^}]+\}$")


def _normalise_frontend_url(raw: str) -> str:
    """Turn a TS template-literal path into a FastAPI-style route shape.

    - Strip the query string (only the path matters for routing).
    - Strip a *trailing* ``${...}`` interpolation that follows a non-slash
      character (almost always a query-string concat like
      ``/archive/files${params}`` where ``params`` is ``""`` or ``?...``).
    - Replace remaining ``${...}`` interpolations with ``{p}`` so all path
      params compare structurally regardless of variable name.
    """
    path = QUERY_STRIP_RE.sub("", raw)
    path = _TRAILING_QS_INTERP_RE.sub("", path)
    path = PARAM_INTERP_RE.sub("{p}", path)
    return path


def _normalise_backend_path(path: str) -> str:
    """FastAPI route paths use ``{name}``; collapse to ``{p}`` for comparison."""
    return PARAM_PLACEHOLDER_RE.sub("{p}", path)


def _scan_method_in_fetch_call(text: str, url_end: int) -> str:
    """Find the ``method:`` option inside the same fetch(...) call as the URL.

    Walk forward from the end of the URL match, tracking ``(`` / ``)`` depth
    starting at 1 (we're inside the fetch call). The call ends when depth
    returns to zero. Search for ``method:`` only within that window —
    prevents grabbing the method of the *next* fetch call.
    """
    depth = 1
    i = url_end
    n = len(text)
    while i < n and depth > 0:
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    window = text[url_end:i]
    m = METHOD_RE.search(window)
    return m.group(1).upper() if m else "GET"


def _collect_frontend_calls() -> list[tuple[str, str, Path, int]]:
    """Walk lib/api/*.ts and yield (METHOD, normalised-path, file, line)."""
    out: list[tuple[str, str, Path, int]] = []
    for ts_file in sorted(API_DIR.glob("*.ts")):
        text = ts_file.read_text(encoding="utf-8")
        for match in URL_RE.finditer(text):
            raw_path = match.group(1)
            line_no = text.count("\n", 0, match.start()) + 1
            method = _scan_method_in_fetch_call(text, match.end())
            out.append((method, _normalise_frontend_url(raw_path), ts_file, line_no))
    return out


@pytest.fixture(scope="module")
def backend_routes() -> set[tuple[str, str]]:
    """Method+path pairs registered on the FastAPI app (normalised)."""
    return {(m, _normalise_backend_path(p)) for (m, p) in _collect_backend_routes()}


def test_every_frontend_fetch_url_maps_to_backend_route(backend_routes):
    """Every ``${MCP_BASE}/...`` fetch in lib/api/ must hit a real route.

    The frontend doesn't import the OpenAPI spec — paths are hardcoded in
    each ``lib/api/*.ts`` module. This test is the only thing standing
    between a renamed/removed route and a 404 in production.
    """
    calls = _collect_frontend_calls()
    assert calls, "lib/api/ extraction returned nothing — fix the regex"

    orphans: list[str] = []
    for method, path, ts_file, line_no in calls:
        if (method, path) in ALLOWLIST:
            continue
        if (method, path) in backend_routes:
            continue
        rel = ts_file.relative_to(REPO_ROOT)
        orphans.append(f"  {method:6s} {path}  ({rel}:{line_no})")

    if orphans:
        pytest.fail(
            "Frontend fetches without a matching backend route:\n"
            + "\n".join(orphans)
            + "\n\nEither the backend route was removed/renamed, or the frontend\n"
              "URL is a typo. Add the route, fix the URL, or — if intentionally\n"
              "out-of-app (proxy, plugin) — extend ALLOWLIST in this test.",
        )


def test_contract_extraction_finds_known_endpoints():
    """Sanity check: the URL regex pulls out the endpoints we know exist."""
    calls = _collect_frontend_calls()
    paths = {p for (_m, p, _f, _l) in calls}
    # These must be present — they're the canaries that prove extraction works.
    expected = {
        "/mcp-servers",
        "/mcp-servers/{p}",
        "/custom-agents",
        "/custom-agents/templates",
        "/custom-agents/from-template/{p}",
        "/admin/kb/duplicates",
    }
    missing = expected - paths
    assert not missing, f"URL regex regressed: didn't find {missing}"
