# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j persistence for the knowledge log (Phase K4.1).

Append-only ledger of wiki-refresh activity. Mirrors Karpathy's
``log.md`` from the LLM-Wiki gist — gives the LLM (and the user) a
chronological view of what the system learned and when, so the wiki
becomes self-navigable.

Schema:

    (:KnowledgeLog {
        log_id: str (uuid hex),
        ts: ISO 8601 timestamp,
        action: "refresh" | "enrich" | "contradict",
        entity_slug: str | null,
        summary: str | null,           # 1-2 sentence change description
        source_artifact_id: str | null,
    })

Indexed by ``ts`` for cheap descending pagination.

Callers: ``app.services.knowledge_log`` (service layer) only.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.graph.knowledge_log")


def append_log_entry(
    driver: Any,
    *,
    action: str,
    entity_slug: str | None,
    summary: str | None = None,
    source_artifact_id: str | None = None,
) -> str:
    """Append one entry to the knowledge log. Returns the log_id."""
    if driver is None:
        return ""

    log_id = uuid.uuid4().hex
    ts = datetime.now(tz=timezone.utc).isoformat()

    try:
        with driver.session() as session:
            session.run(
                """
                CREATE (k:KnowledgeLog {
                    log_id: $log_id,
                    ts: $ts,
                    action: $action,
                    entity_slug: $entity_slug,
                    summary: $summary,
                    source_artifact_id: $source_artifact_id
                })
                """,
                log_id=log_id,
                ts=ts,
                action=action,
                entity_slug=entity_slug or "",
                summary=summary or "",
                source_artifact_id=source_artifact_id or "",
            )
    except Exception as exc:
        log_swallowed_error(
            "graph.knowledge_log.append",
            exc,
            context={"action": action, "entity_slug": entity_slug},
        )
        return ""

    return log_id


def list_log_entries(
    driver: Any,
    *,
    entity_slug: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return paginated log entries, newest first."""
    if driver is None:
        return []

    clauses = []
    params: dict[str, Any] = {"lim": max(1, min(int(limit), 500))}
    if entity_slug:
        clauses.append("k.entity_slug = $slug")
        params["slug"] = entity_slug
    if since:
        clauses.append("k.ts >= $since")
        params["since"] = since
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    try:
        with driver.session() as session:
            result = session.run(
                f"""
                MATCH (k:KnowledgeLog){where}
                RETURN k.log_id AS log_id,
                       k.ts AS ts,
                       k.action AS action,
                       k.entity_slug AS entity_slug,
                       k.summary AS summary,
                       k.source_artifact_id AS source_artifact_id
                ORDER BY k.ts DESC
                LIMIT $lim
                """,
                **params,
            )
            return [dict(row) for row in result]
    except Exception as exc:
        log_swallowed_error(
            "graph.knowledge_log.list",
            exc,
            context={"entity_slug": entity_slug},
        )
        return []
