# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Derived summaries follow their inputs (spec §7).

A wiki entity page is written from the passages that mention the entity, and
a session summary from a conversation's memories. When one of those versions
is superseded, restored or replaced by a new version of its document, the
page and the summary are marked stale and refreshed from current versions.

A forget goes further, because a page or summary may hold the forgotten text:
the page is cleared at once (with its KnowledgeLog excerpts) and queued past
the debounce and a human edit's protection, and the summary goes to the Trash,
or is erased when the forget erases. Call this while the version still has
its MENTIONS and EXTRACTED_FROM edges: a purge removes them.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any, Literal

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.derived")

_SESSION_SUMMARY = "session_summary"

# A document's attachments go with it when it is purged, so their pages count too.
_STALE_PAGES = """
MATCH (r:Artifact) WHERE r.id IN $ids
MATCH (r)-[:HAS_ATTACHMENT*0..]->(:Artifact)-[:MENTIONS]->(e:Entity)
WITH DISTINCT e
SET e.summary_refresh_due = true
FOREACH (_ IN CASE WHEN $clear THEN [1] ELSE [] END |
  SET e.summary = null, e.summary_updated_at = null, e.summary_edited_by = null)
WITH e
OPTIONAL MATCH (k:KnowledgeLog {entity_slug: e.canonical_id})
FOREACH (_ IN CASE WHEN $clear AND k IS NOT NULL THEN [1] ELSE [] END | SET k.summary = '')
RETURN DISTINCT e.canonical_id AS slug
"""

_STALE_SUMMARIES = """
MATCH (a:Artifact)-[:EXTRACTED_FROM]->(c:Conversation)
WHERE a.id IN $ids AND coalesce(a.memory_scope, '') <> $scope
MATCH (s:Artifact {memory_scope: $scope})-[:EXTRACTED_FROM]->(c)
SET s.summary_stale = true
RETURN s.id AS id, coalesce(s.archived, false) AS archived
"""

Forget = Literal["trash", "erase"]


def mark_derived_stale(artifact_ids: Iterable[str | None], *, forget: Forget | None = None) -> dict[str, Any]:
    """Mark what was derived from these versions stale and queue its refresh.

    ``forget`` is set when the versions are being forgotten: then a failure
    raises, so the engine leaves the forget unapplied and retries it, instead
    of letting a purge take the edges that lead to the page. Otherwise a
    failure is logged and the nightly sweep finds the page.
    """
    from app.deps import get_neo4j

    ids = sorted({str(i) for i in artifact_ids if i})
    if not ids:
        return {"pages": 0, "summaries": 0}
    try:
        with get_neo4j().session() as session:
            slugs = [str(r["slug"]) for r in session.run(_STALE_PAGES, ids=ids, clear=forget is not None)
                     if r["slug"]]
            summaries = [dict(r) for r in session.run(_STALE_SUMMARIES, ids=ids, scope=_SESSION_SUMMARY)]
    except Exception as exc:
        if forget is not None:
            raise
        log_swallowed_error("app.services.derived.mark", exc, context={"artifacts": len(ids)})
        return {"pages": 0, "summaries": 0}
    _requeue_pages(slugs, forced=forget is not None)
    if forget is not None:
        _retire_summaries(summaries, erase=forget == "erase")
    return {"pages": len(slugs), "summaries": len(summaries)}


def _requeue_pages(slugs: list[str], *, forced: bool) -> None:
    """A forget refreshes at once; any other change leaves a page a person
    edited recently alone (it stays ``summary_refresh_due``, and the sweep
    honours the same window)."""
    from app.processor.subscribers.wiki_refresh import _human_edit_protected_slugs, enqueue_refresh

    if not forced:
        from app.deps import get_neo4j
        protected = _human_edit_protected_slugs(get_neo4j(), slugs)
        slugs = [slug for slug in slugs if slug not in protected]
    for slug in slugs:
        try:
            enqueue_refresh(slug, force=forced)
        except Exception as exc:  # noqa: BLE001 — summary_refresh_due is set; the sweep picks it up
            log_swallowed_error("app.services.derived.wiki", exc, context={"slug": slug})


def _retire_summaries(summaries: list[dict[str, Any]], *, erase: bool) -> None:
    """A summary of a forgotten memory goes with it, through the engine under
    its own forget (``requested_by: derived``); the scheduler scan writes a new
    one from what is left. Session summaries are not queued here: the scan
    waits for the conversation to go idle."""
    from app.services.forget import engine
    from core.forget.registry import DERIVED, Subject

    ids = [str(s["id"]) for s in summaries if s.get("id") and (erase or not s.get("archived"))]
    if not ids or not engine.forget_available():
        return
    subjects = [Subject("artifact", sid) for sid in ids]
    if erase:
        engine.forget_permanently(subjects, requested_by=DERIVED)
    else:
        engine.trash(subjects, requested_by=DERIVED)
