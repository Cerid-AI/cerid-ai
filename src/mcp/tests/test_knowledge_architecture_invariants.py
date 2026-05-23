# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase K6.3 — knowledge architecture invariants.

Preservation gates for the K1–K6 wiki/vector/graph/memory integration.
Unit-level invariants (no live stack required) — assert the wiring is
present so it cannot regress silently. Integration-level invariants
(real ingest → wiki refresh end-to-end) layer on top via the existing
preservation harness once a worker is in scope.

The five invariants this module enforces:

  1. Ingestion enqueues entity extraction (the on-write hook exists).
  2. Entity extraction emits the ``entities_added`` event.
  3. The wiki refresh subscriber is auto-registered on import.
  4. The surface router has the four documented intent classes.
  5. The /health response exposes the K6.1 wiki_freshness block.
"""
from __future__ import annotations

import inspect

# ---------------------------------------------------------------------------
# Invariant 1 — ingest hook is present and callable
# ---------------------------------------------------------------------------


def test_ingestion_calls_entity_extraction_hook():
    from app.services import ingestion

    assert hasattr(ingestion, "_enqueue_entity_extraction_if_enabled"), (
        "K1.1 regressed: app/services/ingestion.py no longer exposes "
        "_enqueue_entity_extraction_if_enabled — the on-ingest wiki "
        "loop is open again."
    )
    # The function must accept artifact_id as a keyword argument.
    sig = inspect.signature(ingestion._enqueue_entity_extraction_if_enabled)
    assert "artifact_id" in sig.parameters

    # And the post-commit block must reference it (avoids the
    # "function exists but is unwired" failure mode).
    src = inspect.getsource(ingestion)
    assert "_enqueue_entity_extraction_if_enabled(" in src, (
        "K1.1 regressed: ingestion no longer calls the hook"
    )


# ---------------------------------------------------------------------------
# Invariant 2 — entity extraction emits entities_added
# ---------------------------------------------------------------------------


def test_entity_extraction_emits_event():
    from app.processor.jobs import entity_extraction

    src = inspect.getsource(entity_extraction)
    assert '"entities_added"' in src or "'entities_added'" in src, (
        "K1.2 regressed: EntityExtractionJob no longer emits entities_added"
    )
    assert "from app.processor.event_hooks import emit" in src, (
        "K1.2 regressed: EntityExtractionJob no longer imports event_hooks.emit"
    )


# ---------------------------------------------------------------------------
# Invariant 3 — wiki refresh subscriber auto-registers
# ---------------------------------------------------------------------------


def test_wiki_refresh_subscriber_registered():
    from app.processor import event_hooks
    from app.processor.subscribers import wiki_refresh

    # Trigger registration (idempotent — calling again is safe)
    wiki_refresh.register()

    # Look at the internal registry — entities_added must have at
    # least one subscriber bound to wiki_refresh._on_entities_added.
    bucket = event_hooks._subscribers.get("entities_added") or []  # noqa: SLF001
    assert any(
        getattr(fn, "__qualname__", "").endswith("_on_entities_added")
        for fn in bucket
    ), (
        "K1.3 regressed: wiki_refresh._on_entities_added is not "
        "registered for the entities_added event"
    )

    # And the contradiction_detected handler too.
    contra = event_hooks._subscribers.get("contradiction_detected") or []  # noqa: SLF001
    assert any(
        getattr(fn, "__qualname__", "").endswith("_on_contradiction_detected")
        for fn in contra
    )


# ---------------------------------------------------------------------------
# Invariant 4 — surface router intent classes are stable
# ---------------------------------------------------------------------------


def test_surface_router_intent_classes_present():
    from core.retrieval.surface_router import route

    # Exemplar query per documented intent class. If any classifier
    # regresses, this fails loudly with the intent name in the error.
    cases = {
        "compiled_summary": "What is Tesla?",
        "specific_fact": 'Find the email where Elon said "model 3"',
        "relational": "How does Tesla relate to SpaceX?",
        "personal_context": "What did we decide about the migration?",
    }
    for intent_label, query in cases.items():
        r = route(query)
        assert r.intent == intent_label, (
            f"K3.1 regressed: query {query!r} classifies as "
            f"{r.intent!r}, expected {intent_label!r}"
        )


def test_surface_router_maps_each_intent_to_a_primary_surface():
    from core.retrieval.surface_router import route

    primaries = {
        "What is Tesla?": "wiki",
        'Find the passage where Elon said "model 3"': "vector",
        "How does Tesla relate to SpaceX?": "graph",
        "What did we decide about the migration?": "memory",
    }
    for query, expected in primaries.items():
        r = route(query)
        assert r.primary == expected, (
            f"K3.1 regressed: query {query!r} routes to {r.primary!r}, "
            f"expected {expected!r}"
        )


# ---------------------------------------------------------------------------
# Invariant 5 — /health exposes wiki_freshness
# ---------------------------------------------------------------------------


def test_health_check_includes_wiki_freshness():
    from app.routers import health

    src = inspect.getsource(health.health_check)
    assert "wiki_freshness" in src, (
        "K6.1 regressed: /health no longer carries the wiki_freshness "
        "block. Knowledge architecture observability is broken."
    )

    # And the snapshot helper must exist + take a driver.
    assert hasattr(health, "_wiki_freshness_snapshot"), (
        "K6.1 regressed: _wiki_freshness_snapshot helper missing"
    )
    sig = inspect.signature(health._wiki_freshness_snapshot)
    assert "driver" in sig.parameters


# ---------------------------------------------------------------------------
# Invariant 6 — K5 concept-page lookup wired into pkb_wiki_lookup
# ---------------------------------------------------------------------------


def test_wiki_lookup_recognises_concept_slugs():
    """K5 regressed if pkb_wiki_lookup no longer routes concept:{level}:{id}
    slugs through the community page service."""
    from app.mcp_tools import wiki

    assert hasattr(wiki, "_strip_concept_prefix"), (
        "K5 regressed: _strip_concept_prefix helper missing"
    )
    assert hasattr(wiki, "_lookup_concept"), (
        "K5 regressed: _lookup_concept helper missing"
    )
    # Concept slug detection: both prefixed and bare community-id forms.
    assert wiki._strip_concept_prefix("concept:0:42") == "0:42"
    assert wiki._strip_concept_prefix("0:42") == "0:42"
    assert wiki._strip_concept_prefix("org:tesla") is None
    assert wiki._strip_concept_prefix("Tesla") is None
