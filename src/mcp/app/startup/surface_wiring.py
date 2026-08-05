# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""DI wiring for the knowledge surfaces `core.agents.query_agent` consumes.

`core/` must never import `app/`, so the app layer injects these at startup.
This module exists so the wiring has exactly ONE definition: it was previously
inline in ``app/main.py``'s lifespan, which meant any process that was not the
FastAPI app — an eval harness, a script, a REPL — silently ran with the wiki
surface unwired. ``_recall_wiki_surface`` returns ``[]`` when no fetcher is
registered, so a compiled-summary query still answered, just from vector chunks
only, with ``retrieval_meta.wiki_page`` set to ``None``. That is a *degraded*
answer path that looks like a working one, which is precisely how a soak
harness ends up measuring something other than production.

Any caller that drives the answer path outside the FastAPI process must call
:func:`wire_query_surfaces` first.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ai-companion.startup.surface_wiring")


def wire_query_surfaces() -> bool:
    """Register the compiled-wiki-page fetcher (GA P0.5 C2 surface).

    Idempotent — re-registering replaces the slot. Returns True when the
    fetcher was installed, False when wiring failed (the caller keeps running
    with the surface disabled, matching the app's fail-open behaviour).
    """
    try:
        import asyncio

        from app.deps import get_neo4j
        from app.services.wiki_pages import _resolve_entity_slug, get_entity_page
        from core.agents.query_agent import set_wiki_page_fetcher

        async def _fetch_wiki_page(entity_hint: str) -> dict | None:
            driver = get_neo4j()
            if driver is None or not entity_hint:
                return None
            # Resolve, don't slugify. Every canonical_id is type-prefixed
            # (asset:sol, loc:wall-street), so the previous
            # re.sub-to-a-slug + exact-match lookup missed 100% of entities
            # and this surface never returned a page.
            slug = await asyncio.to_thread(_resolve_entity_slug, driver, entity_hint)
            if not slug:
                return None
            page = await get_entity_page(driver, slug)
            if page is None or not page.summary:
                return None
            return {"content": page.summary, "title": page.name, "slug": page.slug}

        set_wiki_page_fetcher(_fetch_wiki_page)
        return True
    except Exception as exc:  # noqa: BLE001 — surface wiring is fail-open
        from core.utils.swallowed import log_swallowed_error

        log_swallowed_error("app.startup.surface_wiring", exc)
        logger.warning("Wiki-page fetcher wiring failed (C2 surface disabled): %s", exc)
        return False
