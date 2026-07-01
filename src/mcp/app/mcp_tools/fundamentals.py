# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 1.5 fundamental tools.

These four tools fill daily-use gaps in the cerid-kb surface:

* ``pkb_artifact_get`` — direct fetch of one artifact + its chunks.
* ``pkb_artifact_delete`` — targeted soft/hard delete (no wholesale-only).
* ``pkb_search_filtered`` — metadata-constrained search (tags, date, source).
* ``pkb_recategorize_bulk`` — wholesale domain moves in one tool call.

All four use the ``@register_tool`` decorator from
``app.tool_registry`` — the canonical pattern for new MCP tools post
v0.95.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import config
from app.db import neo4j as graph
from app.deps import get_chroma, get_neo4j, get_redis
from app.routers.artifacts import recategorize
from app.tool_registry import (
    InvalidParamsError,
    ResourceNotFoundError,
    UpstreamUnavailableError,
    register_tool,
)

# ---------------------------------------------------------------------- pkb_artifact_get

@register_tool(
    name="pkb_artifact_get",
    description=(
        "Fetch one artifact by id along with all its chunks + metadata. "
        "**Use when** you have an `artifact_id` (from `pkb_artifacts`, "
        "`pkb_agent_query.results[*].artifact_id`, or another tool's "
        "output) and need the full content + provenance. "
        "**Returns** `{artifact: {...}, chunks: [{chunk_id, text, "
        "metadata}], chunk_count}`. Errors with code -32004 (not found) "
        "when the id doesn't exist."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "artifact_id": {
                "type": "string",
                "description": "UUID of the artifact (32-hex-with-dashes form)",
            },
            "include_chunks": {
                "type": "boolean",
                "description": "Fetch chunk text from ChromaDB (slower; default true)",
                "default": True,
            },
        },
        "required": ["artifact_id"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "artifact": {
                "type": "object",
                "description": "Neo4j artifact node fields (id, filename, domain, summary, ...)",
            },
            "chunks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "chunk_id": {"type": "string"},
                        "text": {"type": "string"},
                        "metadata": {"type": "object"},
                    },
                },
                "description": "ChromaDB chunks for this artifact (empty when include_chunks=false)",
            },
            "chunk_count": {"type": "integer"},
        },
    },
    cost_class="low",
)
async def pkb_artifact_get(artifact_id: str, include_chunks: bool = True) -> dict[str, Any]:
    """See ``register_tool`` decorator above for the public contract."""
    driver = get_neo4j()
    artifact = await asyncio.to_thread(graph.get_artifact, driver, artifact_id)
    if artifact is None:
        raise ResourceNotFoundError(
            f"Artifact {artifact_id!r} not found in Neo4j"
        )

    chunks_out: list[dict[str, Any]] = []
    if include_chunks:
        chunk_ids_raw = artifact.get("chunk_ids", "[]")
        try:
            chunk_ids = (
                json.loads(chunk_ids_raw)
                if isinstance(chunk_ids_raw, str)
                else (chunk_ids_raw or [])
            )
        except (json.JSONDecodeError, TypeError):
            chunk_ids = []

        if chunk_ids:
            chroma = get_chroma()
            try:
                coll = await asyncio.to_thread(
                    chroma.get_collection,
                    name=config.collection_name(artifact["domain"]),
                )
                fetched = await asyncio.to_thread(
                    coll.get,
                    ids=chunk_ids,
                    include=["documents", "metadatas"],
                )
                for cid, doc, meta in zip(
                    fetched.get("ids", []),
                    fetched.get("documents", []),
                    fetched.get("metadatas", []),
                ):
                    chunks_out.append(
                        {"chunk_id": cid, "text": doc, "metadata": dict(meta or {})}
                    )
            except Exception as exc:
                raise UpstreamUnavailableError(
                    f"ChromaDB unreachable while fetching chunks for "
                    f"{artifact_id!r}: {exc}"
                ) from exc

    return {
        "artifact": artifact,
        "chunks": chunks_out,
        "chunk_count": int(artifact.get("chunk_count") or 0),
    }


# ---------------------------------------------------------------------- pkb_artifact_delete

@register_tool(
    name="pkb_artifact_delete",
    description=(
        "Targeted delete of a single artifact. With `hard=false` "
        "(default) the artifact is **soft-archived**: removed from "
        "default retrieval but recoverable by clearing the `archived` "
        "flag, and chunks remain in ChromaDB for forensic recovery. "
        "With `hard=true` the Neo4j node + all relationships + all "
        "chunks are permanently removed. **Use when** purging a single "
        "bad ingest; for wholesale pack removal use "
        "`pkb_knowledge_pack_uninstall`. **Returns** `{deleted, mode, "
        "artifact_id, domain, filename, chunks_affected}`. Errors with "
        "code -32004 when the id doesn't exist."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "artifact_id": {
                "type": "string",
                "description": "UUID of the artifact to delete",
            },
            "hard": {
                "type": "boolean",
                "description": (
                    "false (default) → soft-archive, reversible; "
                    "true → permanent removal of node + chunks"
                ),
                "default": False,
            },
        },
        "required": ["artifact_id"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "deleted": {"type": "boolean"},
            "mode": {
                "type": "string",
                "description": "'soft' (archived flag set) or 'hard' (rows removed)",
            },
            "artifact_id": {"type": "string"},
            "domain": {"type": "string"},
            "filename": {"type": "string"},
            "chunks_affected": {"type": "integer"},
        },
    },
    cost_class="low",
)
async def pkb_artifact_delete(
    artifact_id: str, hard: bool = False
) -> dict[str, Any]:
    """See decorator for contract.

    Soft-delete: sets ``a.archived = true`` + ``a.archived_at`` on the
    Neo4j node. Retrieval filters out archived artifacts by default
    (the canonical retrieval path drops archived artifacts).
    Chunks stay in ChromaDB so an accidental delete can be reversed by
    clearing the flag.

    Hard-delete: calls ``graph.delete_artifact`` (DETACH DELETE the
    node + relationships), then drops the ChromaDB chunks. Irreversible.
    """
    driver = get_neo4j()

    if not hard:
        # Soft path — set archived flag, leave chunks intact.
        with driver.session() as session:
            result = session.run(
                "MATCH (a:Artifact {id: $id}) "
                "SET a.archived = true, a.archived_at = datetime() "
                "RETURN a.filename AS filename, a.domain AS domain, "
                "a.chunk_count AS chunk_count",
                id=artifact_id,
            )
            row = result.single()
            if row is None:
                raise ResourceNotFoundError(
                    f"Artifact {artifact_id!r} not found"
                )
        return {
            "deleted": True,
            "mode": "soft",
            "artifact_id": artifact_id,
            "domain": row["domain"] or "",
            "filename": row["filename"] or "",
            "chunks_affected": int(row["chunk_count"] or 0),
        }

    # Hard path — full removal.
    record = await asyncio.to_thread(graph.delete_artifact, driver, artifact_id)
    if not record.get("deleted"):
        raise ResourceNotFoundError(
            f"Artifact {artifact_id!r} not found"
        )

    # Drop chunks from ChromaDB. Best-effort: if the collection is
    # missing or the chunks already gone, that's acceptable — the
    # Neo4j node is already gone.
    chunk_ids = record.get("chunk_ids") or []
    chunks_removed = 0
    if chunk_ids and record.get("domain"):
        chroma = get_chroma()
        try:
            coll = await asyncio.to_thread(
                chroma.get_collection,
                name=config.collection_name(record["domain"]),
            )
            await asyncio.to_thread(coll.delete, ids=chunk_ids)
            chunks_removed = len(chunk_ids)
        except Exception:
            # Collection might not exist; tolerate.
            chunks_removed = 0

    return {
        "deleted": True,
        "mode": "hard",
        "artifact_id": artifact_id,
        "domain": record.get("domain", ""),
        "filename": record.get("filename", ""),
        "chunks_affected": chunks_removed,
    }


# ---------------------------------------------------------------------- pkb_search_filtered

@register_tool(
    name="pkb_search_filtered",
    description=(
        "Hybrid retrieval with metadata pre-filters. Like "
        "`pkb_agent_query` but constrained by tags / sub_category / "
        "source pattern / date range before retrieval runs. "
        "**Use when** the user has scope-narrowing context — "
        "'Stripe-related artifacts from March 2026', 'only the rust "
        "docs', etc. Empty filter fields = unconstrained. **Returns** "
        "the same shape as `pkb_agent_query`: `{results, context, "
        "confidence, domains_searched, total_results}`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language search query",
            },
            "domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": f"Domains to search ({', '.join(config.DOMAINS)}). Empty = all.",
            },
            "tag": {
                "type": "string",
                "description": "Single tag to require on matching artifacts (substring match)",
                "default": "",
            },
            "sub_category": {
                "type": "string",
                "description": "Sub-category to require",
                "default": "",
            },
            "source_pattern": {
                "type": "string",
                "description": "Substring match on artifact `client_source` (e.g. 'obsidian', 'gmail')",
                "default": "",
            },
            "after": {
                "type": "string",
                "description": "ISO-8601 lower bound on `ingested_at` (inclusive)",
                "default": "",
            },
            "before": {
                "type": "string",
                "description": "ISO-8601 upper bound on `ingested_at` (exclusive)",
                "default": "",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to retrieve per domain",
                "default": 10,
            },
        },
        "required": ["query"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Chunks matching the filter, sorted by relevance",
            },
            "context": {
                "type": "string",
                "description": "Assembled context string",
            },
            "confidence": {"type": "number"},
            "domains_searched": {"type": "array", "items": {"type": "string"}},
            "total_results": {"type": "integer"},
            "filter_applied": {
                "type": "object",
                "description": "Echo of the active filter for caller verification",
            },
        },
    },
    cost_class="medium",
)
async def pkb_search_filtered(
    query: str,
    domains: list[str] | None = None,
    tag: str = "",
    sub_category: str = "",
    source_pattern: str = "",
    after: str = "",
    before: str = "",
    top_k: int = 10,
) -> dict[str, Any]:
    """Filter-then-retrieve. We pre-narrow via Neo4j list_artifacts
    so the embedding-search hot path doesn't have to evaluate the
    full collection on every call.

    Privacy filter: when private_mode is below the floor declared in
    utils/domain_privacy.py, domain="messages" (iMessage content) is
    stripped from the requested domains. See docs/PRO_MESSAGES.md.
    """
    from core.agents.query_agent import agent_query
    from utils.domain_privacy import (
        DOMAIN_PRIVACY_FLOOR,
        get_global_private_mode_level,
        visible_domains,
    )

    # Apply the privacy filter before any other narrowing. Expand
    # `domains=None` (all domains) to the explicit DOMAINS list so the
    # filter has something to subtract from.
    pm_level = get_global_private_mode_level()
    explicit_domains = domains if domains is not None else list(config.DOMAINS)
    explicit_domains = visible_domains(explicit_domains, pm_level) or []
    if not explicit_domains:
        return {
            "results": [],
            "context": "",
            "confidence": 0.0,
            "domains_searched": [],
            "total_results": 0,
            "filter_applied": {
                "private_mode_floor": pm_level,
                "privacy_gated_domains": sorted(DOMAIN_PRIVACY_FLOOR.keys()),
            },
        }
    domains = explicit_domains

    driver = get_neo4j()

    # Pre-filter on Neo4j metadata when any structured filter is set.
    # If nothing is set, skip the pre-filter (treat as unconstrained).
    has_filter = any([tag, sub_category, source_pattern, after, before])
    pre_filter_ids: set[str] | None = None
    if has_filter:
        list_kwargs: dict[str, Any] = {"limit": 1000}
        if tag:
            list_kwargs["tag"] = tag
        if sub_category:
            list_kwargs["sub_category"] = sub_category
        if source_pattern:
            list_kwargs["client_source"] = source_pattern
        if after:
            list_kwargs["since"] = after

        target_domains = domains or [None]  # type: ignore[list-item]
        all_ids: set[str] = set()
        for d in target_domains:
            kwargs = dict(list_kwargs)
            if d:
                kwargs["domain"] = d
            artifacts = await asyncio.to_thread(
                graph.list_artifacts, driver, **kwargs
            )
            for a in artifacts:
                # If `before` set, post-filter (list_artifacts has no
                # `before` arg today; this keeps the surface narrow).
                if before and a.get("ingested_at"):
                    if a["ingested_at"] >= before:
                        continue
                all_ids.add(a["id"])
        pre_filter_ids = all_ids

        # No matches in pre-filter = empty result, skip retrieval.
        if not pre_filter_ids:
            return {
                "results": [],
                "context": "",
                "confidence": 0.0,
                "domains_searched": domains or list(config.DOMAINS),
                "total_results": 0,
                "filter_applied": {
                    "tag": tag,
                    "sub_category": sub_category,
                    "source_pattern": source_pattern,
                    "after": after,
                    "before": before,
                },
            }

    # Run retrieval; if we had a pre-filter, the post-retrieval pass
    # below trims to only those ids. (A cleaner design pushes the
    # filter into the ChromaDB ``where`` clause; that's a Phase 2.x
    # improvement once we wire the query-agent for it.)
    result = await agent_query(
        query=query,
        domains=domains,
        top_k=top_k,
        use_reranking=True,
        chroma_client=get_chroma(),
        redis_client=get_redis(),
        neo4j_driver=driver,
    )

    if pre_filter_ids is not None:
        result["results"] = [
            r for r in result.get("results", [])
            if r.get("artifact_id") in pre_filter_ids
        ]
        result["total_results"] = len(result["results"])

    result["filter_applied"] = {
        "tag": tag,
        "sub_category": sub_category,
        "source_pattern": source_pattern,
        "after": after,
        "before": before,
    }
    return result


# ---------------------------------------------------------------------- pkb_recategorize_bulk

@register_tool(
    name="pkb_recategorize_bulk",
    description=(
        "Move many artifacts to a new domain in one tool call. Same "
        "atomicity-per-artifact as `pkb_recategorize` — each move is "
        "Neo4j-first, then chunks; failures are reported but don't "
        "abort the batch. **Use when** cleaning up a wave of "
        "mis-domained imports (e.g. 100 markdown files mistakenly "
        "tagged 'general' that belong in 'coding'). **Returns** "
        "`{matched, moved, failed, failures: [{artifact_id, reason}]}`. "
        "Validates the target domain before starting."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "filter": {
                "type": "object",
                "description": (
                    "Filter shape — same fields as `pkb_search_filtered` "
                    "(domain, tag, sub_category, source_pattern, after, "
                    "before). Empty = match everything (DANGEROUS — "
                    "explicit safety: callers must pass non-empty filter)."
                ),
                "properties": {
                    "domain": {"type": "string"},
                    "tag": {"type": "string"},
                    "sub_category": {"type": "string"},
                    "source_pattern": {"type": "string"},
                    "after": {"type": "string"},
                    "before": {"type": "string"},
                },
            },
            "new_domain": {
                "type": "string",
                "description": f"Target domain ({', '.join(config.DOMAINS)})",
            },
            "max_count": {
                "type": "integer",
                "description": (
                    "Safety cap — refuse to move more than this many "
                    "artifacts in one call (default 100, max 1000)."
                ),
                "default": 100,
            },
        },
        "required": ["filter", "new_domain"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "matched": {"type": "integer", "description": "Artifacts matching the filter"},
            "moved": {"type": "integer", "description": "Successfully recategorized"},
            "failed": {"type": "integer"},
            "failures": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "artifact_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "new_domain": {"type": "string"},
        },
    },
    cost_class="medium",
)
async def pkb_recategorize_bulk(
    filter: dict[str, Any],
    new_domain: str,
    max_count: int = 100,
) -> dict[str, Any]:
    """See decorator. Filter validation lives here so the
    "DANGEROUS: empty filter" check fires before any work happens.
    """
    if new_domain not in config.DOMAINS:
        raise InvalidParamsError(
            f"Invalid new_domain {new_domain!r}. Valid: {sorted(config.DOMAINS)}"
        )
    if not filter or not any(filter.values()):
        raise InvalidParamsError(
            "Refusing to bulk-move with empty filter — this would move "
            "every artifact in the KB. Pass at least one of "
            "{domain, tag, sub_category, source_pattern, after, before}."
        )
    if max_count > 1000:
        raise InvalidParamsError(
            f"max_count {max_count} above 1000 hard cap"
        )

    driver = get_neo4j()
    list_kwargs: dict[str, Any] = {"limit": max_count}
    for k in ("domain", "sub_category", "tag", "client_source"):
        v = filter.get(k) or filter.get("source_pattern" if k == "client_source" else k)
        if v:
            list_kwargs[k] = v
    if filter.get("after"):
        list_kwargs["since"] = filter["after"]

    candidates = await asyncio.to_thread(
        graph.list_artifacts, driver, **list_kwargs
    )

    # Post-filter `before` (list_artifacts has no upper bound today).
    before = filter.get("before")
    if before:
        candidates = [
            a for a in candidates
            if a.get("ingested_at") and a["ingested_at"] < before
        ]

    if len(candidates) > max_count:
        # Defensive: list_artifacts is supposed to honour limit but
        # double-check before mutating anything.
        candidates = candidates[:max_count]

    moved = 0
    failures: list[dict[str, str]] = []
    for a in candidates:
        artifact_id = a["id"]
        if a.get("domain") == new_domain:
            failures.append({
                "artifact_id": artifact_id,
                "reason": f"already in domain {new_domain!r}",
            })
            continue
        try:
            await asyncio.to_thread(
                recategorize,
                artifact_id=artifact_id,
                new_domain=new_domain,
                tags="",
            )
            moved += 1
        except Exception as exc:
            failures.append({
                "artifact_id": artifact_id,
                "reason": str(exc),
            })

    return {
        "matched": len(candidates),
        "moved": moved,
        "failed": len(failures),
        "failures": failures,
        "new_domain": new_domain,
    }
