# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-0 verifiability harness — the ROUTE-REGISTRATION probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 4.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``.

CR-017: ``POST /chat/compress`` is registered TWICE — ``agents.py:238``
(``compress_history_endpoint``, returns ``messages``) and ``chat.py:627``
(``compress_context``, returns ``compressed_messages``). ``agents.router`` is
mounted before ``chat.router`` (main.py ``_api_routers`` order), and Starlette
matches in registration order, so the ``chat.py`` handler is permanently
unreachable dead code. Offline probe over the two routers' route tables.

E1 Phase 4 CLOSED CR-017: chat.py's shadowed ``compress_context`` handler and
its ``ContextCompressRequest`` / ``CompressContextResponse`` schemas were
removed, leaving the single reachable ``agents.py`` registration. The probe is
now a live gate (auto-tagged ``preservation`` by the package conftest).
"""
from __future__ import annotations


def _compress_post_routes(router) -> list:
    return [
        r for r in router.routes
        if getattr(r, "path", "") == "/chat/compress"
        and "POST" in (getattr(r, "methods", None) or set())
    ]


def test_chat_compress_registered_exactly_once():
    from app.routers import agents, chat

    total = len(_compress_post_routes(agents.router)) + len(_compress_post_routes(chat.router))
    assert total == 1, (
        f"POST /chat/compress is registered {total} times across agents.py + "
        f"chat.py — the later registration is shadowed dead code (CR-017)"
    )


def test_green_anchor_chat_compress_is_registered_somewhere():
    """A reachable POST /chat/compress must exist (guards against the CR-017 fix
    deleting BOTH registrations). Holds now and after."""
    from app.routers import agents, chat

    total = len(_compress_post_routes(agents.router)) + len(_compress_post_routes(chat.router))
    assert total >= 1, "no POST /chat/compress route registered at all"
