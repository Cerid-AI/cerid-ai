# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Route introspection that survives FastAPI's include_router change."""


def iter_app_routes(app):
    """Yield every route reachable from ``app``, flattening included routers.

    FastAPI 0.141 stopped flattening ``include_router`` into ``app.routes``:
    it appends a single ``_IncludedRouter`` instead. Routing and
    ``app.openapi()`` are unaffected, but a flat ``for r in app.routes`` no
    longer sees anything a router contributed.

    That silence is the hazard. A membership check ("is this path
    registered?") starts failing honestly, but an absence check ("this router
    contributes no path under that prefix") starts passing vacuously — it
    would pass even if the routes were present. Both forms exist in this
    suite, so the walk lives in one place rather than being re-derived per
    call site.

    ``effective_route_contexts()`` is the accessor to use rather than
    ``original_router.routes``: the latter reports the child's own paths with
    the ``include_router(prefix=...)`` NOT applied, so a prefixed mount would
    be matched under the wrong path. The contexts carry ``.path`` and
    ``.methods`` like a route does.

    Works on both the flat (<=0.140) and nested (>=0.141) shapes.
    """
    seen: set[int] = set()

    def _walk(routes):
        for route in routes:
            if id(route) in seen:
                continue
            seen.add(id(route))

            contexts = getattr(route, "effective_route_contexts", None)
            if callable(contexts):          # FastAPI >= 0.141 _IncludedRouter
                yield from contexts()
                continue

            if getattr(route, "path", None) is not None:
                yield route

            # Mount / sub-application (both eras).
            child = getattr(route, "routes", None)
            if isinstance(child, (list, tuple)):
                yield from _walk(child)

    yield from _walk(app.routes)
