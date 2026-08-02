# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Entity-hint → canonical_id resolution for the compiled-summary surface.

Every ``canonical_id`` in the graph is type-prefixed (``asset:sol``,
``loc:wall-street``) — 2,558 of 2,558 summarised entities. Both call sites that
fetch a compiled wiki page used to pass a naively-slugified query hint straight
into an exact ``canonical_id`` lookup, so "What is SOL?" looked up ``sol``,
missed ``asset:sol``, and the wiki surface returned nothing. Every
compiled-summary answer silently degraded to vector-only while
``surface_route.primary`` still reported ``"wiki"``.

Measured against the live graph before the fix:
    sol             -> None
    asset:sol       -> page, 542 chars
    wall-street     -> None
    loc:wall-street -> page, 866 chars
"""
from __future__ import annotations

import inspect
from typing import Any


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def single(self) -> dict[str, Any] | None:
        return self._row


class _Session:
    def __init__(self, capture: list[dict[str, Any]], row: dict[str, Any] | None) -> None:
        self._capture = capture
        self._row = row

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *_: Any) -> bool:
        return False

    def run(self, cypher: str, **kw: Any) -> _Result:
        self._capture.append({"cypher": cypher, **kw})
        return _Result(self._row)


class _Driver:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._row = row

    def session(self, **_: Any) -> _Session:
        return _Session(self.calls, self._row)


class TestResolveEntitySlug:
    def test_returns_prefixed_canonical_id(self):
        from app.services.wiki_pages import _resolve_entity_slug

        d = _Driver(row={"cid": "asset:sol", "rank": 1})
        assert _resolve_entity_slug(d, "SOL") == "asset:sol"

    def test_normalises_hint_to_slug_and_name(self):
        """Multi-word hints must reach both the slug and the name predicate."""
        from app.services.wiki_pages import _resolve_entity_slug

        d = _Driver(row={"cid": "loc:wall-street", "rank": 1})
        _resolve_entity_slug(d, "  Wall Street  ")

        params = d.calls[0]
        assert params["slug"] == "wall-street"
        assert params["name"] == "wall street"

    def test_prefers_exact_then_suffix_then_name(self):
        """Ranking must be expressed in the query, not left to Neo4j's ordering."""
        from app.services.wiki_pages import _resolve_entity_slug

        d = _Driver(row={"cid": "x", "rank": 0})
        _resolve_entity_slug(d, "anything")

        cypher = d.calls[0]["cypher"]
        assert "e.canonical_id = $slug" in cypher
        assert "split(e.canonical_id, ':')[-1] = $slug" in cypher
        assert "toLower(coalesce(e.name, '')) = $name" in cypher
        # Deterministic tie-break — two types can share a suffix.
        assert "ORDER BY rank" in cypher
        assert "mention_count" in cypher

    def test_no_match_returns_none(self):
        from app.services.wiki_pages import _resolve_entity_slug

        assert _resolve_entity_slug(_Driver(row=None), "nothing-here") is None

    def test_blank_hint_never_queries(self):
        from app.services.wiki_pages import _resolve_entity_slug

        d = _Driver(row={"cid": "x", "rank": 0})
        assert _resolve_entity_slug(d, "   ") is None
        assert _resolve_entity_slug(d, "") is None
        assert _resolve_entity_slug(d, "!!!") is None
        assert d.calls == [], "a blank hint must not reach the database"


class TestBothCallSitesResolve:
    """Two independent fetchers exist; a fix to one does not cover the other.

    They were found broken separately — the C2 fetcher slugified with re.sub,
    and pkb_answer_with_citations passed the raw hint through untouched.
    """

    def test_c2_surface_fetcher_resolves(self):
        from app.startup import surface_wiring

        src = inspect.getsource(surface_wiring.wire_query_surfaces)
        assert "_resolve_entity_slug" in src
        # The naive slugify that caused the miss must not come back. Match a
        # CALL specifically — the surrounding comment names `re.sub` to explain
        # the historical bug, and that prose should stay.
        assert "re.sub(" not in src

    def test_answer_tool_resolves(self):
        from app.mcp_tools import retrieval

        src = inspect.getsource(retrieval.pkb_answer_with_citations)
        assert "_resolve_entity_slug" in src
        assert "get_entity_page(\n                get_neo4j(), surface_decision.matched_entity_hint," not in src


class TestSurfaceWiringIsShared:
    """The wiring must have one definition, or out-of-process callers silently
    run with the wiki surface disabled and measure a degraded path."""

    def test_main_delegates_rather_than_inlining(self):
        import app.main as main_mod

        src = inspect.getsource(main_mod)
        assert "wire_query_surfaces" in src
        assert "set_wiki_page_fetcher" not in src, (
            "main.py must delegate to app.startup.surface_wiring, not re-wire inline"
        )

    def test_wiring_is_importable_and_idempotent(self):
        from app.startup.surface_wiring import wire_query_surfaces

        assert callable(wire_query_surfaces)
