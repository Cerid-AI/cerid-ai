# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Wiki MCP tools.

``pkb_wiki_lookup`` fetches an entity or concept page by slug
(fuzzy-matched on miss). Three depth levels: ``summary`` (light),
``full`` (+related, sources, contradictions), ``with_refs`` (+external).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.deps import get_neo4j
from app.tool_registry import (
    InvalidParamsError,
    ResourceNotFoundError,
    UpstreamUnavailableError,
    register_tool,
)
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.mcp_tools.wiki")


def _fuzzy_match_slug(driver: Any, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Find candidate entities by name or partial slug match.

    Used when the caller provides a non-canonical input (e.g. "Tesla"
    instead of "org:tesla"). Runs a case-insensitive contains query
    against ``name`` and ``canonical_id``. Ordered by mention_count
    DESC so the most-active match wins.
    """
    query_lc = query.lower()
    with driver.session() as session:
        result = session.run(
            """
            MATCH (e:Entity)
            WHERE toLower(e.name) CONTAINS $q
               OR toLower(e.canonical_id) CONTAINS $q
            RETURN e.canonical_id AS slug,
                   e.name AS name,
                   e.entity_type AS entity_type,
                   coalesce(e.mention_count, 0) AS mention_count,
                   (e.summary IS NOT NULL) AS has_summary
            ORDER BY mention_count DESC
            LIMIT $lim
            """,
            q=query_lc,
            lim=limit,
        )
        return [
            {
                "slug": row["slug"],
                "name": row["name"],
                "entity_type": row["entity_type"],
                "mention_count": int(row["mention_count"]),
                "has_summary": bool(row["has_summary"]),
            }
            for row in result
        ]


@register_tool(
    name="pkb_wiki_lookup",
    description=(
        "Look up the canonical wiki page for an entity (person, "
        "organization, place) OR a concept page (Leiden community of "
        "co-mentioned entities, identified by `concept:{level}:{native_id}` "
        "slug, or by the community-side `{level}:{native_id}` form). "
        "**Use when** the user asks 'who is X?' / 'what is X?' / "
        "'what's the concept around Y?' — returns the pre-compiled "
        "summary instead of re-deriving from raw chunks. Cheaper "
        "and more coherent than a fresh `pkb_agent_query` for "
        "compiled-summary intents. **Returns** `{found: bool, "
        "kind: 'entity'|'concept'|null, page: ... | null, "
        "candidates: [{slug, name, entity_type|level, "
        "mention_count|member_count, has_summary}]}` — `candidates` "
        "is populated when fuzzy matching surfaces more than one "
        "possibility or when the page lacks a summary. `depth` "
        "controls payload weight: `summary` (default), `full` (+ "
        "related + source artifacts + contradictions for entities; "
        "+ member entities for concepts), `with_refs` (+ external "
        "Wikipedia/Wikidata references for entity pages only)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_or_topic": {
                "type": "string",
                "description": "Canonical slug (e.g. 'org:tesla') or fuzzy name ('Tesla').",
            },
            "depth": {
                "type": "string",
                "enum": ["summary", "full", "with_refs"],
                "default": "summary",
            },
        },
        "required": ["entity_or_topic"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "found": {"type": "boolean"},
            "kind": {"type": ["string", "null"], "enum": ["entity", "concept", None]},
            "page": {
                "type": ["object", "null"],
                "description": "WikiEntityPage or CommunityFull shape; see OpenAPI.",
            },
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string"},
                        "name": {"type": "string"},
                        "entity_type": {"type": "string"},
                        "mention_count": {"type": "integer"},
                        "has_summary": {"type": "boolean"},
                    },
                },
            },
        },
        "required": ["found", "candidates"],
    },
    cost_class="low",
)
async def pkb_wiki_lookup(
    entity_or_topic: str,
    depth: str = "summary",
) -> dict[str, Any]:
    if not entity_or_topic or not entity_or_topic.strip():
        raise InvalidParamsError("entity_or_topic must be a non-empty string")
    if depth not in ("summary", "full", "with_refs"):
        raise InvalidParamsError(
            f"depth must be summary|full|with_refs, got {depth!r}"
        )

    query = entity_or_topic.strip()
    driver = get_neo4j()
    if driver is None:
        raise UpstreamUnavailableError("Neo4j unavailable")

    # Concept slugs (concept:{level}:{native_id} or bare {level}:{native_id})
    # route to community lookup before entity work.
    concept_id = _strip_concept_prefix(query)
    if concept_id is not None:
        return await _lookup_concept(driver, concept_id, depth)

    # Try direct entity slug lookup.
    from app.services.wiki_pages import get_entity_page  # noqa: PLC0415

    try:
        page = await get_entity_page(driver, query)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "mcp_tools.wiki.lookup.direct",
            exc,
            context={"query": query},
        )
        page = None

    # On miss, fuzzy-match for slug candidates.
    if page is None:
        try:
            candidates = await asyncio.to_thread(_fuzzy_match_slug, driver, query)
        except Exception as exc:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "mcp_tools.wiki.lookup.fuzzy",
                exc,
                context={"query": query},
            )
            candidates = []

        if not candidates:
            raise ResourceNotFoundError(
                f"No entity matched {query!r} (tried direct slug + fuzzy name)."
            )

        # If a single high-confidence candidate, auto-resolve.
        if len(candidates) == 1 and candidates[0]["has_summary"]:
            page = await get_entity_page(driver, candidates[0]["slug"])

        if page is None:
            return {"found": False, "kind": None, "page": None, "candidates": candidates}

    # Trim payload based on depth.
    page_dict = page.model_dump() if hasattr(page, "model_dump") else dict(page)
    if depth == "summary":
        page_dict = {
            k: v for k, v in page_dict.items()
            if k in (
                "slug", "name", "entity_type", "summary",
                "last_updated_at", "next_refresh_due", "confidence_band",
            )
        }
    elif depth == "full":
        page_dict.pop("external_references", None)

    return {"found": True, "kind": "entity", "page": page_dict, "candidates": []}


# Concept page lookup (Leiden communities as wiki entities) ------------------


def _strip_concept_prefix(query: str) -> str | None:
    """Return ``community_id`` if ``query`` is a concept slug, else ``None``.

    Accepts ``concept:0:42`` or bare ``0:42``. Format is strictly
    ``{level}:{native_id}`` with both parts non-negative integers.
    """
    candidate = query[len("concept:"):] if query.startswith("concept:") else query
    if ":" not in candidate:
        return None
    head, tail = candidate.split(":", 1)
    if not head.isdigit() or not tail.isdigit():
        return None
    return candidate


async def _lookup_concept(driver: Any, community_id: str, depth: str) -> dict[str, Any]:
    """Concept page from a Leiden community. Raises ResourceNotFoundError on miss."""
    from app.services.community_pages import get_community_page  # noqa: PLC0415

    try:
        community = await get_community_page(driver, community_id)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "mcp_tools.wiki.lookup.concept",
            exc,
            context={"community_id": community_id},
        )
        community = None

    if community is None:
        raise ResourceNotFoundError(
            f"No concept (community) matched {community_id!r}."
        )

    page_dict = community.model_dump() if hasattr(community, "model_dump") else dict(community)
    # Normalize to the wiki-page interface shape so the LLM sees a
    # consistent envelope regardless of entity vs concept kind.
    summary_fields = {
        "slug": f"concept:{page_dict.get('id', community_id)}",
        "name": page_dict.get("title") or page_dict.get("name") or f"Concept {community_id}",
        "entity_type": "CONCEPT",
        "summary": page_dict.get("summary"),
        "last_updated_at": page_dict.get("summary_generated_at") or page_dict.get("updated_at"),
        "next_refresh_due": None,
        "confidence_band": "unknown",
    }
    if depth == "summary":
        return {"found": True, "kind": "concept", "page": summary_fields, "candidates": []}

    # full / with_refs return the richer shape with member entities
    summary_fields["members"] = page_dict.get("members", [])
    summary_fields["member_count"] = page_dict.get("member_count", 0)
    summary_fields["level"] = page_dict.get("level", 0)
    return {"found": True, "kind": "concept", "page": summary_fields, "candidates": []}
