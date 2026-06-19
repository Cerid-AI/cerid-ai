# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Graph visualization API — neighborhood + path + community endpoints.

Phase A of the 2026-05-21 Cerid v1.0 systemic implementation plan. This
router exposes the read-only graph queries the new Subjects pane needs
to render Atlas + Constellation + Timeline modes.

Endpoints (this commit ships just neighborhood; embeddings_3d / timeline /
communities / path / tour follow in Phases B / M):

  GET /graph/neighborhood?entity=<id>&hops=<1|2|3>&filter=<type>
    Returns the focal entity's K-hop neighborhood as
    {nodes: [...], edges: [...]} ready for sigma.js consumption.

Performance posture (validated 2026-05-21 research):
  - apoc.neighbors.byhop deduplicates + avoids relationship-pattern
    explosion; ~5-30ms at 50K-node KB
  - Degree cap: skip nodes with > MAX_DEGREE (default 500) to prevent
    hub-induced p99 spikes
  - LRU cache: Redis SETEX 60s on (entity, hops, filter_hash) → JSON payload

Visual encoding shape (per cerid-visualization-spec.md §2.2):
  node = {id, name, type, community, mention_count, trust_state,
          recency_score, focused?}
  edge = {source, target, type, weight, attestation, contradiction?}
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.deps import get_neo4j, get_redis
from config.features import is_feature_enabled
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.graph")
router = APIRouter(prefix="/graph", tags=["graph"])

# Cache lifetime — short because graphs change as users ingest. Per
# validation: 60s is a good balance. Override via env for test infra.
_NEIGHBORHOOD_TTL_SECONDS = int(os.getenv("GRAPH_NEIGHBORHOOD_CACHE_TTL", "60"))
_MAX_DEGREE = int(os.getenv("GRAPH_MAX_NODE_DEGREE", "500"))
_MAX_HOPS = 3

# 24h for the 3D projection — recomputed nightly by the
# compute_umap_3d job. Override for tests.
_EMBEDDINGS_3D_TTL_SECONDS = int(os.getenv("GRAPH_EMBEDDINGS_3D_CACHE_TTL", "86400"))
# Hard cap on entity count per response — protects against accidental
# unbounded scans. The Constellation client renders at most ~10K nodes
# before LOD downsampling would kick in.
_EMBEDDINGS_3D_MAX = int(os.getenv("GRAPH_EMBEDDINGS_3D_MAX", "10000"))
_EMBEDDINGS_3D_MAX_LINKS = int(os.getenv("GRAPH_EMBEDDINGS_3D_MAX_LINKS", "25000"))


class GraphNode(BaseModel):
    """Visual node shape consumed by sigma.js + Atlas renderer."""
    id: str
    name: str
    type: str
    community: str | None = None
    mention_count: int = 0
    trust_state: str = "unknown"  # verified / partial / unverified / contradicted / unknown
    recency_score: float = 0.0    # 0..1, recent mentions push toward 1
    focused: bool = False
    primary_domain: str | None = None


class GraphEdge(BaseModel):
    """Visual edge shape consumed by sigma.js + Atlas renderer."""
    source: str
    target: str
    type: str = "mentions"        # mentions / works_on / discussed_with / contradicts / temporal
    weight: float = 1.0           # log(co_mentions+1) normalized 0..1
    attestation: str = "inferred" # attested / inferred — default honest: an edge
    # without an explicit attestation is inferred (auto-extracted), not attested.
    contradiction: bool = False


class NeighborhoodResponse(BaseModel):
    """Shape returned by GET /graph/neighborhood."""
    focal_entity: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool = False       # true if hit MAX_DEGREE or hop limit
    cached: bool = False          # true if served from Redis LRU


def _cache_key(entity: str, hops: int, filter_str: str) -> str:
    """Stable cache key from query params. filter_str hashed to bound length."""
    if filter_str:
        # SHA1 here is a *cache-key hash*, not a security primitive.
        # usedforsecurity=False disables bandit B324 + signals intent.
        h = hashlib.sha1(  # noqa: S324
            filter_str.encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()[:8]
        return f"cerid:graph:nbhd:{entity}:{hops}:{h}"
    return f"cerid:graph:nbhd:{entity}:{hops}"


def _safe_int_props(record: dict, key: str, default: int = 0) -> int:
    """Neo4j may return None for missing properties; coerce safely."""
    v = record.get(key)
    return int(v) if v is not None else default


def _safe_float_props(record: dict, key: str, default: float = 0.0) -> float:
    v = record.get(key)
    return float(v) if v is not None else default


@router.get("/neighborhood", response_model=NeighborhoodResponse)
async def get_neighborhood(
    entity: str = Query(..., description="Focal entity ID (canonical_id from KB)"),
    hops: int = Query(2, ge=1, le=_MAX_HOPS, description="Hop depth (1-3)"),
    filter: str | None = Query(None, description="Optional entity-type filter (Person|Project|Topic|...)"),
) -> NeighborhoodResponse:
    """K-hop neighborhood of an entity, shaped for Atlas WebGL renderer.

    Cache: Redis LRU with TTL ``GRAPH_NEIGHBORHOOD_CACHE_TTL`` (default 60s).
    Degree cap: nodes with > ``GRAPH_MAX_NODE_DEGREE`` (default 500) edges
    skipped — guards against hub-induced p99 spikes per
    cerid-visualization-spec.md §9.3.

    Tier gating: this endpoint is community-tier (Atlas is everyday-view
    surface). Pro-tier extensions (lenses, time scrubbing) are layered on
    via separate query params + endpoints in Phases B/M.

    Returns shape consumed directly by sigma.js + graphology adapter on
    the frontend.
    """
    if not entity:
        raise HTTPException(status_code=400, detail="entity is required")

    redis = get_redis()
    cache_key = _cache_key(entity, hops, filter or "")

    # 1. Cache fast-path
    if redis:
        try:
            cached_raw = redis.get(cache_key)
            if cached_raw:
                payload = json.loads(
                    cached_raw if isinstance(cached_raw, str)
                    else cached_raw.decode("utf-8"),
                )
                payload["cached"] = True
                return NeighborhoodResponse(**payload)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            # silent-catch-allowed: cache read failure is non-fatal —
            # fall through to Neo4j. Logged at INFO (not exception) because
            # a corrupt cache row is operationally noisy but not a bug.
            logger.info("graph.cache_read_miss: %s", exc)

    # 2. Cache miss → query Neo4j
    nodes, edges, truncated = await _query_neighborhood(entity, hops, filter)
    if not nodes:
        raise HTTPException(status_code=404, detail=f"entity '{entity}' not found")

    # Mark the focal node so the renderer can apply focus visual treatment
    for node in nodes:
        if node.id == entity:
            node.focused = True
            break

    response = NeighborhoodResponse(
        focal_entity=entity, nodes=nodes, edges=edges, truncated=truncated, cached=False,
    )

    # 3. Populate cache (best-effort)
    if redis:
        try:
            redis.set(
                cache_key,
                response.model_dump_json(),
                ex=_NEIGHBORHOOD_TTL_SECONDS,
            )
        except (OSError, ValueError) as exc:
            # silent-catch-allowed: cache write failure is non-fatal —
            # next request will re-query Neo4j. Operationally surfaced
            # at INFO; metrics catch any pattern via Sentry httpx tagging.
            logger.info("graph.cache_write_failed: %s", exc)

    return response


async def _query_neighborhood(
    entity: str, hops: int, filter: str | None,
) -> tuple[list[GraphNode], list[GraphEdge], bool]:
    """Run the Cypher neighborhood expansion. Pure I/O — split out for testability."""
    driver = get_neo4j()
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j unavailable")

    # Cypher: typed CO_MENTIONED traversal (Neo4j 5.x). Restricting to
    # [:CO_MENTIONED] prevents paths routing through Community/Artifact hub
    # nodes which inflate hop-2/3 result sets to O(community size). Degree
    # cap uses COUNT {} subquery — size() over a pattern was removed in 5.x.
    # Per-hop intermediate cap ($hop_degree) limits expansion through hubs
    # that have high CO_MENTIONED degree without fully excluding them.
    # Filter narrows by entity_type if specified.
    type_filter = ""
    if filter:
        type_filter = "AND (e.entity_type = $filter OR n.entity_type = $filter)"

    # Partition reachable entities by CO_MENTIONED degree in a single pass:
    # keep those under the cap as `related`, and surface `truncated` when any
    # reachable entity was dropped by the cap. This replaces a Python-side
    # check that compared the wrong property (mention_count vs CO_MENTIONED
    # degree) over only the surviving nodes, so it never fired. No extra
    # expansion cost — the cap previously just discarded already-expanded
    # endpoints in the WHERE.
    cypher = f"""
        MATCH (n:Entity {{canonical_id: $entity}})
        OPTIONAL MATCH (n)-[:CO_MENTIONED*1..{hops}]-(e:Entity)
        WHERE e.canonical_id IS NOT NULL {type_filter}
        WITH n, e, COUNT {{ (e)-[:CO_MENTIONED]-() }} AS deg
        WITH n,
             collect(DISTINCT CASE WHEN deg < $max_degree THEN e END) AS related_raw,
             sum(CASE WHEN deg >= $max_degree THEN 1 ELSE 0 END) AS dropped_count
        WITH n,
             [x IN related_raw WHERE x IS NOT NULL] AS related,
             dropped_count > 0 AS truncated
        UNWIND ([n] + related) AS node
        OPTIONAL MATCH (node)-[r]-(other:Entity)
        WHERE other IN ([n] + related)
        WITH DISTINCT
            node,
            truncated,
            collect(DISTINCT {{
                from: startNode(r).canonical_id,
                to:   endNode(r).canonical_id,
                type: type(r),
                weight: coalesce(r.weight, 1),
                attestation: coalesce(r.attestation, 'inferred'),
                contradiction: coalesce(r.contradiction, false)
            }}) AS edges_for_node
        RETURN
            node.canonical_id AS id,
            node.name AS name,
            node.type AS type,
            node.community_id AS community,
            node.mention_count AS mention_count,
            node.trust_state AS trust_state,
            node.recency_score AS recency_score,
            node.primary_domain AS primary_domain,
            edges_for_node AS edges,
            truncated AS truncated
    """

    try:
        with driver.session() as session:
            result = session.run(
                cypher,
                entity=entity,
                filter=filter,
                max_degree=_MAX_DEGREE,
            )
            rows = result.data()
    except (OSError, RuntimeError, ValueError) as exc:
        logger.exception("Neighborhood query failed for entity=%s: %s", entity, exc)
        raise HTTPException(status_code=500, detail="Graph query failed") from exc

    nodes: list[GraphNode] = []
    edges_map: dict[tuple[str, str, str], GraphEdge] = {}

    for row in rows:
        node_id = row.get("id")
        if not node_id:
            continue
        nodes.append(GraphNode(
            id=node_id,
            name=row.get("name") or node_id,
            type=row.get("type") or "unknown",
            community=row.get("community"),
            mention_count=_safe_int_props(row, "mention_count"),
            trust_state=row.get("trust_state") or "unknown",
            recency_score=_safe_float_props(row, "recency_score"),
            primary_domain=row.get("primary_domain"),
        ))
        for e in row.get("edges") or []:
            src = e.get("from")
            tgt = e.get("to")
            etype = e.get("type") or "mentions"
            if not src or not tgt:
                continue
            # Canonical edge orientation — sort endpoints so duplicate
            # bidirectional edges collapse.
            key = (min(src, tgt), max(src, tgt), etype)
            if key not in edges_map:
                edges_map[key] = GraphEdge(
                    source=src,
                    target=tgt,
                    type=etype,
                    weight=float(e.get("weight") or 1),
                    attestation=e.get("attestation") or "inferred",
                    contradiction=bool(e.get("contradiction")),
                )

    # Truncation signal computed at query time: True iff a reachable entity
    # was dropped by the CO_MENTIONED degree cap (same value on every row).
    truncated = bool(rows and rows[0].get("truncated"))

    return nodes, list(edges_map.values()), truncated


# ── Phase M Day 1-2 — timeline endpoint ─────────────────────────────


class TimelineBucket(BaseModel):
    """One time-sliced row of activity for the Timeline mode."""

    date: str  # ISO-8601 day (YYYY-MM-DD) or YYYY-MM week-start / YYYY-MM month
    mention_count: int
    entities_introduced: int = 0  # count of entities first seen on this date


class TimelineResponse(BaseModel):
    """Shape returned by GET /graph/timeline.

    Designed for the Timeline mode's scrub cursor: each bucket is one
    discrete step the cursor lands on. Granularity auto-adapts so a
    fresh KB shows day-level resolution while a multi-year archive
    shows month-level (capped at ~365 buckets to keep the timeline
    affordance navigable).
    """

    entity: str | None = None     # null = global timeline (whole graph)
    from_date: str                # ISO-8601
    to_date: str                  # ISO-8601
    granularity: str              # "day" | "week" | "month"
    buckets: list[TimelineBucket]
    total_mentions: int
    total_entities_introduced: int
    cached: bool = False


def _resolve_granularity(window_days: int, requested: str | None) -> str:
    """Auto-pick day/week/month so the cursor doesn't drown in too many
    buckets. Operator can override via the `granularity` query param."""
    if requested in ("day", "week", "month"):
        return requested
    if window_days <= 90:
        return "day"
    if window_days <= 365:
        return "week"
    return "month"


_GRANULARITY_PREFIX = {
    "day": 10,      # "2026-05-22"
    "week": 7,      # "2026-05" (approximation — true week buckets in post-process)
    "month": 7,     # "2026-05"
}


def _bucket_key(iso_ts: str, granularity: str) -> str:
    """Bucket an ISO timestamp into the canonical key for `granularity`."""
    if not iso_ts:
        return ""
    if granularity == "day":
        return iso_ts[:10]
    if granularity == "month":
        return iso_ts[:7]
    # Week: compute Monday of the week the timestamp lives in
    try:
        d = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        monday = d - timedelta(days=d.weekday())
        return monday.date().isoformat()
    except (ValueError, TypeError):
        return iso_ts[:10]


@router.get("/timeline", response_model=TimelineResponse)
async def get_timeline(
    entity: str | None = Query(
        None,
        description="Optional focal entity id. Omit for a global timeline.",
    ),
    from_date: str | None = Query(
        None,
        description="ISO-8601 lower bound. Default = now - period.",
        alias="from",
    ),
    to_date: str | None = Query(
        None,
        description="ISO-8601 upper bound. Default = now.",
        alias="to",
    ),
    period: str = Query(
        "30d",
        description="Convenience: 7d / 30d / 90d / 365d. Ignored when from/to set.",
    ),
    granularity: str | None = Query(
        None,
        description="day / week / month. Auto-picked when null.",
    ),
) -> TimelineResponse:
    """Time-bucketed mention activity over a window, optionally focused
    on a single entity.

    Each bucket carries:
      - ``mention_count``: how many MENTIONS edges fired in the bucket
      - ``entities_introduced``: how many entities had their first
        mention in the bucket (the "birth date" used by Timeline mode
        for node enter/exit animations)

    Cache: Redis LRU with 60s TTL — matches the /graph/neighborhood
    pattern so the Timeline mode's scrub feels instant within a session.
    """
    # Resolve the window
    now = datetime.now(tz=timezone.utc)
    period_days = _parse_period(period)
    end_dt = _parse_iso_or(to_date, now)
    start_dt = _parse_iso_or(
        from_date,
        end_dt - timedelta(days=period_days),
    )
    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="to must be after from")

    window_days = max(1, (end_dt - start_dt).days)
    if window_days > 730:
        raise HTTPException(status_code=400, detail="window exceeds 730 days")
    gran = _resolve_granularity(window_days, granularity)

    cache_key = (
        f"cerid:graph:timeline:"
        f"{entity or 'global'}:{start_dt.isoformat()}:{end_dt.isoformat()}:{gran}"
    )

    # Cache check
    try:
        from app.deps import get_redis
        redis = get_redis()
        if redis is not None:
            cached = redis.get(cache_key)
            if cached is not None:
                import json
                payload = json.loads(
                    cached.decode() if isinstance(cached, bytes) else cached,
                )
                payload["cached"] = True
                return TimelineResponse(**payload)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.routers.graph.timeline_cache_read",
            exc,
            context={"cache_key": cache_key},
        )
        redis = None

    # Pull mention events from Neo4j
    try:
        from app.deps import get_neo4j
        driver = get_neo4j()
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.routers.graph.timeline_neo4j_unavailable",
            exc,
        )
        return TimelineResponse(
            entity=entity,
            from_date=start_dt.isoformat(),
            to_date=end_dt.isoformat(),
            granularity=gran,
            buckets=[],
            total_mentions=0,
            total_entities_introduced=0,
        )

    # Aggregate per DAY in Cypher (count(*)) rather than returning one row
    # per MENTIONS edge. The result then scales with the number of days in
    # the window (<= 730), not the number of mention edges (millions on a
    # mature corpus) — the per-edge form materialised the whole edge set in
    # memory. Python rolls the day buckets up to the requested granularity.
    if entity:
        # Per-entity timeline: count mentions FROM Artifact TO this entity
        cypher = (
            "MATCH (a:Artifact)-[m:MENTIONS]->(e:Entity {canonical_id: $entity}) "
            "WHERE m.created_at >= $start AND m.created_at <= $end "
            "RETURN substring(m.created_at, 0, 10) AS ts, false AS is_birth, count(*) AS c "
            "UNION ALL "
            "MATCH (e:Entity {canonical_id: $entity}) "
            "WHERE e.created_at >= $start AND e.created_at <= $end "
            "RETURN substring(e.created_at, 0, 10) AS ts, true AS is_birth, count(*) AS c"
        )
        params = {
            "entity": entity,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
        }
    else:
        # Global timeline: every mention edge + every entity birth
        cypher = (
            "MATCH (a:Artifact)-[m:MENTIONS]->(e:Entity) "
            "WHERE m.created_at >= $start AND m.created_at <= $end "
            "RETURN substring(m.created_at, 0, 10) AS ts, false AS is_birth, count(*) AS c "
            "UNION ALL "
            "MATCH (e:Entity) "
            "WHERE e.created_at >= $start AND e.created_at <= $end "
            "RETURN substring(e.created_at, 0, 10) AS ts, true AS is_birth, count(*) AS c"
        )
        params = {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
        }

    try:
        import asyncio
        rows = await asyncio.to_thread(_run_timeline_cypher, driver, cypher, params)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.routers.graph.timeline_cypher_failed",
            exc,
            context={"entity": entity or "", "granularity": gran},
        )
        return TimelineResponse(
            entity=entity,
            from_date=start_dt.isoformat(),
            to_date=end_dt.isoformat(),
            granularity=gran,
            buckets=[],
            total_mentions=0,
            total_entities_introduced=0,
        )

    # Bucket. ``c`` is the per-day count from the aggregated query; it
    # defaults to 1 so a per-edge row shape (used in tests / legacy callers)
    # still tallies one mention each.
    by_bucket: dict[str, dict[str, int]] = {}
    for row in rows:
        ts = row.get("ts")
        if not ts:
            continue
        key = _bucket_key(str(ts), gran)
        if not key:
            continue
        c = int(row.get("c", 1) or 1)
        bucket = by_bucket.setdefault(key, {"mention_count": 0, "entities_introduced": 0})
        if row.get("is_birth"):
            bucket["entities_introduced"] += c
        else:
            bucket["mention_count"] += c

    buckets = [
        TimelineBucket(
            date=key,
            mention_count=v["mention_count"],
            entities_introduced=v["entities_introduced"],
        )
        for key, v in sorted(by_bucket.items())
    ]

    response = TimelineResponse(
        entity=entity,
        from_date=start_dt.isoformat(),
        to_date=end_dt.isoformat(),
        granularity=gran,
        buckets=buckets,
        total_mentions=sum(b.mention_count for b in buckets),
        total_entities_introduced=sum(b.entities_introduced for b in buckets),
    )

    # Cache for 60s
    if redis is not None:
        try:
            import json
            redis.setex(
                cache_key,
                int(os.getenv("GRAPH_TIMELINE_CACHE_TTL", "60")),
                json.dumps(response.model_dump()),
            )
        except Exception as exc:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.routers.graph.timeline_cache_write",
                exc,
                context={"cache_key": cache_key},
            )

    return response


def _parse_period(period: str) -> int:
    """Parse 7d / 30d / 90d / 365d into integer days."""
    if not period or not period.endswith("d"):
        return 30
    try:
        n = int(period[:-1])
        return max(1, min(730, n))
    except ValueError:
        return 30


def _parse_iso_or(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ISO date: {value!r}") from exc


def _run_timeline_cypher(driver: Any, cypher: str, params: dict) -> list[dict]:
    if driver is None:
        return []
    try:
        with driver.session() as session:
            return [dict(r) for r in session.run(cypher, params)]
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.routers.graph.timeline_cypher_exec_failed",
            exc,
        )
        return []


@router.get("/health")
async def graph_health() -> dict[str, Any]:
    """Lightweight liveness probe for the graph subsystem. Returns config
    + dep readiness without running an actual graph query."""
    driver = get_neo4j()
    return {
        "neo4j_available": driver is not None,
        "cache_ttl_seconds": _NEIGHBORHOOD_TTL_SECONDS,
        "max_node_degree": _MAX_DEGREE,
        "max_hops": _MAX_HOPS,
        "visualization_enabled": is_feature_enabled("live_metrics"),
    }


# ---------------------------------------------------------------------------
# Embeddings 3D (Constellation — Phase B Day 3)
# ---------------------------------------------------------------------------


class EntityEmbedding3D(BaseModel):
    """One 3D-projected entity for Constellation rendering."""
    id: str
    name: str
    x: float
    y: float
    z: float
    type: str = "unknown"
    community: str | None = None
    mention_count: int = 0
    trust_state: str = "unknown"
    projection: str = "fallback"  # "umap" or "fallback" (pre-backfill)
    primary_domain: str | None = None


class Embeddings3DResponse(BaseModel):
    count: int
    entities: list[EntityEmbedding3D]
    # CO_MENTIONED linkage as compact [source_idx, target_idx, weight]
    # triples indexing into ``entities``. Index-based (not id-based) to keep
    # the payload small at 16K+ edges; safe because the response is built and
    # cached atomically, so indices can't drift from the entity list.
    links: list[tuple[int, int, float]] = []
    cached: bool = False
    computed_at: str | None = None


def _embeddings_3d_cache_key(filter_str: str, entities_csv: str) -> str:
    # v3 suffix: payload gained `primary_domain` (Cycle 1 domain backbone) —
    # versioning prevents a Domain lens showing all-"other" silently for 24 h.
    # The shared bust pattern cerid:graph:emb3d:* still matches.
    if filter_str or entities_csv:
        h = hashlib.sha1(  # noqa: S324
            f"{filter_str}|{entities_csv}".encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()[:12]
        return f"cerid:graph:emb3d:v3:{h}"
    return "cerid:graph:emb3d:v3:all"


@router.get("/embeddings/3d", response_model=Embeddings3DResponse)
async def get_embeddings_3d(
    entities: str | None = Query(None, description="Comma-separated subset of canonical_ids"),
    filter: str | None = Query(None, description="Optional entity-type filter"),
) -> Embeddings3DResponse:
    """3D-projected entity coordinates for Constellation rendering.

    Returns ``umap_x/y/z`` from each Entity node if the compute_umap_3d
    job has run, else falls back to a deterministic community-cluster
    layout so the renderer always has coords to work with.

    Cache: Redis SETEX 24h (configurable via
    ``GRAPH_EMBEDDINGS_3D_CACHE_TTL``).
    """
    redis = get_redis()
    entities_csv = entities or ""
    cache_key = _embeddings_3d_cache_key(filter or "", entities_csv)

    # Cache fast-path
    if redis:
        try:
            cached_raw = redis.get(cache_key)
            if cached_raw:
                payload = json.loads(
                    cached_raw if isinstance(cached_raw, str)
                    else cached_raw.decode("utf-8"),
                )
                payload["cached"] = True
                return Embeddings3DResponse(**payload)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            # silent-catch-allowed: cache miss is non-fatal — re-fetch.
            logger.info("graph.emb3d.cache_read_miss: %s", exc)

    # Build the entity-id whitelist (if any)
    entity_ids = (
        [e.strip() for e in entities_csv.split(",") if e.strip()]
        if entities_csv else None
    )

    rows = await _query_embeddings_3d(filter, entity_ids)

    computed_at_values: list[str] = [
        str(r["computed_at"]) for r in rows if r.get("computed_at")
    ]
    computed_at: str | None = max(computed_at_values) if computed_at_values else None

    payload_entities = [
        EntityEmbedding3D(
            id=r["id"],
            name=r.get("name") or r["id"],
            x=float(r.get("x") or 0.0),
            y=float(r.get("y") or 0.0),
            z=float(r.get("z") or 0.0),
            type=r.get("type") or "unknown",
            community=r.get("community"),
            mention_count=int(r.get("mention_count") or 0),
            trust_state=r.get("trust_state") or "unknown",
            projection=r.get("method") or "fallback",
            primary_domain=r.get("primary_domain"),
        )
        for r in rows
    ]

    links = await _query_embeddings_3d_links(
        [r["id"] for r in rows],
    )

    response = Embeddings3DResponse(
        count=len(payload_entities),
        entities=payload_entities,
        links=links,
        cached=False,
        computed_at=computed_at,
    )

    if redis:
        try:
            redis.set(
                cache_key,
                response.model_dump_json(),
                ex=_EMBEDDINGS_3D_TTL_SECONDS,
            )
        except (OSError, ValueError) as exc:
            # silent-catch-allowed: cache-write failure non-fatal.
            logger.info("graph.emb3d.cache_write_failed: %s", exc)

    return response


async def _query_embeddings_3d_links(
    scope_ids: list[str],
) -> list[tuple[int, int, float]]:
    """CO_MENTIONED edges between in-scope entities, as index triples.

    Pulls the strongest edges (ORDER BY weight DESC, capped) where both
    endpoints carry umap coords, then keeps only pairs whose endpoints are
    in ``scope_ids`` and maps ids → indices into the caller's entity list.
    One unparameterized-shape query — scoping happens in Python so the
    filtered/subset variants reuse it unchanged.
    """
    driver = get_neo4j()
    if driver is None or not scope_ids:
        return []

    cypher = """
        MATCH (a:Entity)-[r:CO_MENTIONED]->(b:Entity)
        WHERE a.umap_x IS NOT NULL AND b.umap_x IS NOT NULL
        RETURN
            a.canonical_id AS s,
            b.canonical_id AS t,
            coalesce(r.weight, 1.0) AS w
        ORDER BY w DESC
        LIMIT $max_links
    """

    def _run() -> list[dict[str, Any]]:
        with driver.session() as session:
            return list(session.run(cypher, max_links=_EMBEDDINGS_3D_MAX_LINKS).data())

    try:
        edge_rows = await asyncio.to_thread(_run)
    except (OSError, RuntimeError, ValueError) as exc:
        # silent-catch-allowed: links are an enhancement layer — a failed
        # edge query must not take down the node payload.
        logger.warning("emb3d links query failed: %s", exc)
        return []

    index_of = {eid: i for i, eid in enumerate(scope_ids)}
    links: list[tuple[int, int, float]] = []
    for row in edge_rows:
        si = index_of.get(str(row.get("s") or ""))
        ti = index_of.get(str(row.get("t") or ""))
        if si is None or ti is None or si == ti:
            continue
        links.append((si, ti, float(row.get("w") or 1.0)))
    return links


async def _query_embeddings_3d(
    filter: str | None,
    entity_ids: list[str] | None,
) -> list[dict[str, Any]]:
    """Read entity 3D coords from Neo4j. Entities without umap_* fields
    are excluded — the compute_umap_3d job populates them.

    Returns each row as a plain dict with x/y/z/method/computed_at."""
    driver = get_neo4j()
    if driver is None:
        return []

    where_clauses = ["e.canonical_id IS NOT NULL"]
    params: dict[str, Any] = {"max_entities": _EMBEDDINGS_3D_MAX}
    if filter:
        where_clauses.append("(e.entity_type = $filter OR e.type = $filter)")
        params["filter"] = filter
    if entity_ids:
        where_clauses.append("e.canonical_id IN $entity_ids")
        params["entity_ids"] = entity_ids

    where = " AND ".join(where_clauses)

    cypher = f"""
        MATCH (e:Entity)
        WHERE {where}
        RETURN
            e.canonical_id AS id,
            coalesce(e.name, e.canonical_id) AS name,
            coalesce(e.entity_type, e.type, 'unknown') AS type,
            e.community_id AS community,
            coalesce(e.mention_count, 0) AS mention_count,
            coalesce(e.trust_state, 'unknown') AS trust_state,
            e.umap_x AS x,
            e.umap_y AS y,
            e.umap_z AS z,
            coalesce(e.umap_method, 'fallback') AS method,
            e.umap_computed_at AS computed_at,
            e.primary_domain AS primary_domain
        LIMIT $max_entities
    """

    def _run() -> list[dict[str, Any]]:
        with driver.session() as session:
            return list(session.run(cypher, **params).data())

    try:
        rows = await asyncio.to_thread(_run)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.exception("emb3d query failed: %s", exc)
        raise HTTPException(status_code=500, detail="Embedding query failed") from exc

    # Drop rows where the projection job hasn't computed coords yet.
    # Constellation renders only entities with valid coords.
    return [r for r in rows if r.get("x") is not None and r.get("y") is not None and r.get("z") is not None]


# ---------------------------------------------------------------------------
# Graph map (Cartographer Phase 0) — community hulls + layout artifact
# ---------------------------------------------------------------------------

_COMMUNITY_MAP_REDIS_KEY = "cerid:graph:map:communities"
_GRAPH_MAP_CACHE_KEY = "cerid:graph:emb3d:v3:map"


class MapCommunity(BaseModel):
    """One Leiden community in the cartographic map artifact."""

    id: str
    count: int
    hull: list[tuple[float, float]]
    anchor: tuple[float, float]
    label: str
    top_hubs: list[dict[str, Any]]
    trust_mix: dict[str, int]


class GraphMapResponse(BaseModel):
    """Shape returned by GET /graph/map.

    Bundles entity positions, CO_MENTIONED links, and precomputed community
    artifacts into a single cached payload for the Constellation renderer.
    """

    count: int
    entities: list[EntityEmbedding3D]
    links: list[tuple[int, int, float]]
    communities: list[MapCommunity]
    silhouette: float | None = None
    computed_at: str | None = None
    cached: bool = False
    layout_fallback: bool = False


# Per-layout cache keys — keyed under the emb3d wildcard bust pattern
# cerid:graph:emb3d:* so the nightly job invalidates all of them.
# Omitting the ?layout param is byte-identical to ?layout=force.
_VALID_LAYOUTS = frozenset({"force", "wells", "domain"})
_LAYOUT_MAP_CACHE_KEY_TMPL = "cerid:graph:emb3d:v3:map:{layout}"
_LAYOUT_COMMUNITY_REDIS_KEY_TMPL = "cerid:graph:map:communities:{layout}"


@router.get("/map", response_model=GraphMapResponse)
async def get_graph_map(
    layout: str | None = Query(
        default=None,
        description="Layout basis: force (default), wells, or domain. "
                    "Omitting is byte-identical to force. Unknown value → 422.",
    ),
) -> GraphMapResponse:
    """Full cartographic map payload for Constellation.

    Bundles the 2D-projected entity positions, CO_MENTIONED edge links, and
    the precomputed community hull/anchor/trust-mix artifacts from the
    compute_umap_3d nightly job into one cached response.

    Supports ``?layout=force|wells|domain``.  Omitting the parameter is
    byte-identical to ``?layout=force``.  Unknown values return 422.

    Cache: Redis SETEX ``GRAPH_EMBEDDINGS_3D_CACHE_TTL`` (default 24h).
    Per-layout cache keys ``cerid:graph:emb3d:v3:map:{layout}`` match the
    ``cerid:graph:emb3d:*`` bust pattern so a job recompute invalidates all
    of them automatically.

    ``layout_fallback: true`` is returned when the requested non-default
    layout artifact is missing — the response falls back to the force layout.

    Community artifacts degrade to ``communities=[]`` when the nightly job
    has not yet written the Redis artifact — the entity+link payload is
    always returned.

    Use when: the Constellation renderer needs entity positions + community
    hulls for the full cartographic map view.

    Returns: count, entities (id/name/x/y/z/type/community/mention_count/
    trust_state/projection/primary_domain), links (index triples), communities
    (id/count/hull/anchor/label/top_hubs/trust_mix), silhouette, computed_at,
    cached, layout_fallback.
    """
    # Validate layout parameter
    if layout is not None and layout not in _VALID_LAYOUTS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown layout '{layout}'. Valid values: {sorted(_VALID_LAYOUTS)}.",
        )

    effective_layout = layout or "force"
    is_non_default = effective_layout != "force"
    layout_fallback = False

    cache_key = _LAYOUT_MAP_CACHE_KEY_TMPL.format(layout=effective_layout)
    community_redis_key = _LAYOUT_COMMUNITY_REDIS_KEY_TMPL.format(
        layout=effective_layout
    )

    redis = get_redis()

    # Cache fast-path — per-layout key.
    if redis:
        try:
            cached_raw = redis.get(cache_key)
            if cached_raw:
                payload = json.loads(
                    cached_raw if isinstance(cached_raw, str)
                    else cached_raw.decode("utf-8"),
                )
                payload["cached"] = True
                return GraphMapResponse(**payload)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            # silent-catch-allowed: cache miss is non-fatal — re-fetch.
            logger.info("graph.map.cache_read_miss layout=%s: %s", effective_layout, exc)

    # For non-default layouts: check if per-layout position artifact exists in
    # Redis. If missing, fall back to the force layout with layout_fallback=True.
    if is_non_default and redis:
        layout_pos_key = f"cerid:graph:emb3d:v3:layout_positions:{effective_layout}"
        try:
            has_layout_pos = redis.exists(layout_pos_key)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("app.routers.graph.map_layout_check", exc)
            has_layout_pos = False
        if not has_layout_pos:
            layout_fallback = True
            # Fall through to force layout positions
            cache_key = _LAYOUT_MAP_CACHE_KEY_TMPL.format(layout="force")
            community_redis_key = _LAYOUT_COMMUNITY_REDIS_KEY_TMPL.format(layout="force")
            # Try the force cache first
            if redis:
                try:
                    cached_raw = redis.get(cache_key)
                    if cached_raw:
                        payload = json.loads(
                            cached_raw if isinstance(cached_raw, str)
                            else cached_raw.decode("utf-8"),
                        )
                        payload["cached"] = True
                        payload["layout_fallback"] = True
                        return GraphMapResponse(**payload)
                except (json.JSONDecodeError, ValueError, OSError) as exc:
                    logger.info("graph.map.fallback_cache_read_miss: %s", exc)

    # Entity positions and links.
    rows = await _query_embeddings_3d(None, None)

    computed_at_values = [str(r["computed_at"]) for r in rows if r.get("computed_at")]
    computed_at: str | None = max(computed_at_values) if computed_at_values else None

    # For non-force layouts, load per-layout positions and override x/y/z.
    layout_pos_override: dict[str, list[float]] = {}
    if is_non_default and not layout_fallback and redis:
        try:
            pos_raw = redis.get(f"cerid:graph:emb3d:v3:layout_positions:{effective_layout}")
            if pos_raw:
                layout_pos_override = json.loads(
                    pos_raw if isinstance(pos_raw, str) else pos_raw.decode("utf-8")
                )
        except Exception as exc:  # noqa: BLE001 — position override is best-effort
            log_swallowed_error("app.routers.graph.map_layout_positions", exc)

    payload_entities = [
        EntityEmbedding3D(
            id=r["id"],
            name=r.get("name") or r["id"],
            x=float(layout_pos_override[r["id"]][0] if r["id"] in layout_pos_override else (r.get("x") or 0.0)),
            y=float(layout_pos_override[r["id"]][1] if r["id"] in layout_pos_override else (r.get("y") or 0.0)),
            z=float(layout_pos_override[r["id"]][2] if r["id"] in layout_pos_override else (r.get("z") or 0.0)),
            type=r.get("type") or "unknown",
            community=r.get("community"),
            mention_count=int(r.get("mention_count") or 0),
            trust_state=r.get("trust_state") or "unknown",
            projection=r.get("method") or "fallback",
            primary_domain=r.get("primary_domain"),
        )
        for r in rows
    ]

    links = await _query_embeddings_3d_links([r["id"] for r in rows])

    # Community artifacts — degrade gracefully if missing.
    # Try per-layout key first, fall back to the legacy key.
    communities: list[MapCommunity] = []
    silhouette: float | None = None
    if redis:
        try:
            raw = redis.get(community_redis_key) or redis.get(_COMMUNITY_MAP_REDIS_KEY)
            if raw:
                artifact = json.loads(
                    raw if isinstance(raw, str) else raw.decode("utf-8"),
                )
                silhouette = artifact.get("silhouette")
                for c in artifact.get("communities") or []:
                    communities.append(MapCommunity(
                        id=c["id"],
                        count=c["count"],
                        hull=[tuple(p) for p in c.get("hull") or []],  # type: ignore[misc]
                        anchor=tuple(c.get("anchor") or [0.0, 0.0]),  # type: ignore[arg-type]
                        label=c.get("label") or c["id"],
                        top_hubs=c.get("top_hubs") or [],
                        trust_mix=c.get("trust_mix") or {},
                    ))
        except Exception as exc:  # noqa: BLE001 — community read is non-fatal
            log_swallowed_error("app.routers.graph.map_community_read", exc)

    response = GraphMapResponse(
        count=len(payload_entities),
        entities=payload_entities,
        links=links,
        communities=communities,
        silhouette=silhouette,
        computed_at=computed_at,
        cached=False,
        layout_fallback=layout_fallback,
    )

    if redis:
        try:
            redis.set(
                cache_key,
                response.model_dump_json(),
                ex=_EMBEDDINGS_3D_TTL_SECONDS,
            )
        except (OSError, ValueError) as exc:
            # silent-catch-allowed: cache-write failure non-fatal.
            logger.info("graph.map.cache_write_failed layout=%s: %s", effective_layout, exc)

    return response


# ---------------------------------------------------------------------------
# Stratigraph — Timeline v2 (Phase M)
# ---------------------------------------------------------------------------

_STRATA_TTL_SECONDS = 60
_STRATA_TOP_COMMUNITIES = 8
_STRATA_TOP_TRACKS = 40
_STRATA_MAX_BUCKETS = 365
_STRATA_WINDOW_CAP = 730
_TRACK_MAX_EVENTS = 500
_TRACK_CO_MENTIONED_CAP = 20
_TRUST_STATES = frozenset({"verified", "partial", "unverified", "unknown"})
# Tephra Cycle-2: event payload caps per spec (<=200/window, <=8/bucket)
_STRATA_MAX_EVENTS_PER_WINDOW = 200
_STRATA_MAX_EVENTS_PER_BUCKET = 8
# Cache key version — bump when the cached payload shape breaks consumers.
# v1 (original) → v2 (Tephra Cycle-2: lanes[], events[], top_entities, data_extent)
_STRATA_CACHE_VERSION = "v2"


class StrataTopEntity(BaseModel):
    """One top entity in a per-(lane, bucket) slot."""
    name: str
    slug: str


class StrataEventItem(BaseModel):
    """One event entry in the strata events[] list (Tephra Cycle-2)."""
    kind: str        # "refresh" | "enrich" | "contradict" | "contradiction_finding"
    ts: str          # ISO-8601
    lane_id: str     # domain name or community_id
    bucket: str      # bucket key (same format as bucket_dates)
    entity_slug: str = ""
    entity_name: str = ""
    summary: str = ""  # ≤140 chars
    severity: str = ""  # for contradiction_finding: "low" | "medium" | "high"
    source_artifact_id: str = ""


class StrataVerificationAgg(BaseModel):
    """Aggregated verification-report counts per (lane, bucket) (Tephra Cycle-2)."""
    lane_id: str
    bucket: str
    count: int
    verified: int
    unverified: int
    uncertain: int
    overall_score_avg: float


class StrataLaneMeta(BaseModel):
    """Server-side lane metadata for Timeline canvas labels (amendment #8)."""
    lane_id: str
    label: str
    icon: str = "file"   # lucide kebab-name from taxonomy; "file" fallback
    summary_short: str = ""
    summary_full: str = ""


class StrataCommunity(BaseModel):
    """One community entry in the /graph/timeline/strata response."""
    community_id: str
    label: str
    color_slot: int
    trust_mix: dict[str, float]
    total_mentions: int
    is_other: bool = False


class StrataSeriesRow(BaseModel):
    """Per-(community, entity_type, domain) mention buckets aligned to bucket_dates."""
    community_id: str
    entity_type: str
    domain: str
    buckets: list[int]
    unverified_buckets: list[int]


class StrataTrack(BaseModel):
    """One top-DOI entity track."""
    canonical_id: str
    name: str
    entity_type: str
    community_id: str
    trust_state: str
    first_seen: str
    rank: int
    total_mentions: int
    buckets: list[int]
    primary_domain: str | None = None


class StrataMarker(BaseModel):
    date: str
    kind: str       # "ingest_burst" | "birth_surge"
    count: int
    lane_id: str = ""  # Tephra Cycle-2: per-lane attribution ("" = global)


class StrataTotals(BaseModel):
    mentions: int
    entities_introduced: int


class StrataResponse(BaseModel):
    """Shape returned by GET /graph/timeline/strata (Tephra Cycle-2 extended)."""
    from_date: str
    to_date: str
    granularity: str
    bucket_dates: list[str]
    communities: list[StrataCommunity]
    series: list[StrataSeriesRow]
    tracks: list[StrataTrack]
    markers: list[StrataMarker]
    totals: StrataTotals
    cached: bool = False
    # Tephra Cycle-2 additive fields — all optional so old cached payloads still parse
    lanes: list[StrataLaneMeta] = []
    events: list[StrataEventItem] = []
    verification_aggs: list[StrataVerificationAgg] = []
    # Per-(lane, bucket) top entities for the hover tooltip (≤3 per slot)
    top_entities: dict[str, list[StrataTopEntity]] = {}  # key: "{lane_id}:{bucket}"
    # Earliest data timestamp across the window — lets the frontend implement
    # data-extent clamping for the 180d default window (amendment #7)
    data_extent_from: str | None = None
    # Earliest KnowledgeLog entry — events before this date were never
    # recorded; drives the pre-ledger hairline (honesty load-bearing)
    ledger_start_date: str | None = None


class TrackEvent(BaseModel):
    ts: str
    artifact_id: str
    artifact_filename: str
    confidence: float
    summary: str
    co_mentioned: list[dict[str, str]]


class TrackKnowledgeEvent(BaseModel):
    """A KnowledgeLog event surfaced in the track detail (Tephra Cycle-2)."""
    kind: str        # "refresh" | "enrich" | "contradict"
    ts: str
    entity_slug: str = ""
    summary: str = ""
    source_artifact_id: str = ""


class TrackNewEntity(BaseModel):
    """An entity born in this bucket window (Tephra Cycle-2)."""
    name: str
    slug: str
    created_at: str


class TrackVerification(BaseModel):
    """Aggregated VerificationReport counts for this entity track (Tephra Cycle-2)."""
    reports: int = 0
    verified: int = 0
    unverified: int = 0
    uncertain: int = 0
    overall_score_avg: float = 0.0


class TrackDetailResponse(BaseModel):
    """Shape returned by GET /graph/timeline/track/{canonical_id} (Tephra Cycle-2)."""
    canonical_id: str
    name: str
    events: list[TrackEvent]
    cached: bool = False
    # Tephra Cycle-2 additive fields — all optional for backward compat
    knowledge_events: list[TrackKnowledgeEvent] = []
    new_entities: list[TrackNewEntity] = []
    verification: TrackVerification = TrackVerification()
    community_summary: str = ""  # populated under community lens


def _strata_cache_key(start: str, end: str, gran: str) -> str:
    # Version bump v1→v2: Tephra Cycle-2 extended payload (lanes, events,
    # top_entities, verification_aggs) would break old-shape consumers if
    # they read an unversioned cache entry from before this deploy.
    return f"cerid:graph:timeline:strata:{_STRATA_CACHE_VERSION}:{start}:{end}:{gran}"


def _track_cache_key(canonical_id: str, start: str, end: str, bucket: str = "") -> str:
    suffix = f":{bucket}" if bucket else ""
    return f"cerid:graph:timeline:track:{canonical_id}:{start}:{end}{suffix}"


def _color_slot_from_id(community_id: str) -> int:
    """Deterministic community→slot (0-7) compatible with communitySlot() on client."""
    h = hashlib.sha1(  # noqa: S324
        community_id.encode("utf-8"),
        usedforsecurity=False,
    ).digest()
    return int.from_bytes(h[:4], "big") % 8


def _derive_markers(
    bucket_dates: list[str],
    mention_counts: list[int],
    birth_counts: list[int],
    *,
    lane_id: str = "",
) -> list[StrataMarker]:
    """Derive ingest_burst and birth_surge markers per spec.

    Rule: count > max(20, 3 × median of non-zero buckets).

    When ``lane_id`` is non-empty the returned markers carry it so the
    frontend can attribute the burst to a specific domain lane (Tephra
    Cycle-2, amendment #1 per-lane attribution).  Global markers have
    ``lane_id=""`` and are retained only for the community lens code path.
    """
    def _threshold(counts: list[int]) -> float:
        nonzero = [c for c in counts if c > 0]
        med = statistics.median(nonzero) if nonzero else 0.0
        return max(20.0, 3.0 * med)

    mention_thresh = _threshold(mention_counts)
    birth_thresh = _threshold(birth_counts)

    markers: list[StrataMarker] = []
    for date, m_count, b_count in zip(bucket_dates, mention_counts, birth_counts):
        if m_count > mention_thresh:
            markers.append(StrataMarker(date=date, kind="ingest_burst", count=m_count, lane_id=lane_id))
        if b_count > birth_thresh:
            markers.append(StrataMarker(date=date, kind="birth_surge", count=b_count, lane_id=lane_id))
    return markers


def _build_bucket_dates(start_dt: datetime, end_dt: datetime, gran: str) -> list[str]:
    """Generate the canonical list of bucket keys covering [start_dt, end_dt)."""
    dates: list[str] = []
    cursor = start_dt.date()
    end_date = end_dt.date()
    if gran == "day":
        while cursor <= end_date and len(dates) < _STRATA_MAX_BUCKETS:
            dates.append(cursor.isoformat())
            cursor += timedelta(days=1)
    elif gran == "week":
        # Snap to Monday
        cursor = cursor - timedelta(days=cursor.weekday())
        while cursor <= end_date and len(dates) < _STRATA_MAX_BUCKETS:
            dates.append(cursor.isoformat())
            cursor += timedelta(weeks=1)
    else:  # month
        year, month = cursor.year, cursor.month
        while len(dates) < _STRATA_MAX_BUCKETS:
            key = f"{year:04d}-{month:02d}"
            dates.append(key)
            if f"{year:04d}-{month:02d}" >= f"{end_date.year:04d}-{end_date.month:02d}":
                break
            month += 1
            if month > 12:
                month = 1
                year += 1
    return dates


def _doi_score(
    in_window_mentions: int,
    last_bucket_idx: int,
    total_buckets: int,
    trust_state: str,
) -> float:
    """DOI = ln(1 + mentions) + 0.5·recency + 0.5·unverified_attention."""
    recency = 0.5 if (total_buckets > 0 and last_bucket_idx >= total_buckets * 2 // 3) else 0.0
    attention = 0.5 if trust_state == "unverified" else 0.0
    return math.log1p(in_window_mentions) + recency + attention


def _run_strata_cypher(driver: Any, cypher: str, params: dict) -> list[dict]:
    if driver is None:
        return []
    try:
        with driver.session() as session:
            return [dict(r) for r in session.run(cypher, **params).data()]
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.routers.graph.strata_cypher_exec",
            exc,
        )
        return []


@router.get("/timeline/strata", response_model=StrataResponse)
async def get_timeline_strata(
    from_date: str | None = Query(None, alias="from", description="ISO-8601 lower bound"),
    to_date: str | None = Query(None, alias="to", description="ISO-8601 upper bound"),
    period: str = Query("90d", description="7d/30d/90d/365d. Ignored when from/to set."),
    granularity: str | None = Query(None, description="day/week/month. Auto when null."),
) -> StrataResponse:
    """Stratigraph strata payload for Timeline v2.

    Returns per-community per-entity_type bucketed mention series, top-40
    DOI entity tracks, ingest_burst/birth_surge markers, and community
    metadata (label, trust_mix) sourced from the compute_umap_3d community
    artifact in Redis.

    Cache: Redis 60s TTL keyed on all params.
    Degrades to empty on Neo4j or Redis failure — never 500.
    """
    now = datetime.now(tz=timezone.utc)
    period_days = _parse_period(period)
    end_dt = _parse_iso_or(to_date, now)
    start_dt = _parse_iso_or(from_date, end_dt - timedelta(days=period_days))
    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="to must be after from")
    window_days = max(1, (end_dt - start_dt).days)
    if window_days > _STRATA_WINDOW_CAP:
        raise HTTPException(status_code=400, detail="window exceeds 730 days")
    gran = _resolve_granularity(window_days, granularity)
    bucket_dates = _build_bucket_dates(start_dt, end_dt, gran)
    if len(bucket_dates) > _STRATA_MAX_BUCKETS:
        bucket_dates = bucket_dates[:_STRATA_MAX_BUCKETS]

    cache_key = _strata_cache_key(start_dt.isoformat(), end_dt.isoformat(), gran)
    redis = get_redis()

    # Cache fast-path
    try:
        if redis is not None:
            cached_raw = redis.get(cache_key)
            if cached_raw is not None:
                payload = json.loads(
                    cached_raw.decode() if isinstance(cached_raw, bytes) else cached_raw,
                )
                payload["cached"] = True
                return StrataResponse(**payload)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.routers.graph.strata_cache_read",
            exc,
            context={"cache_key": cache_key},
        )
        redis = get_redis()  # re-obtain after error path

    _empty = StrataResponse(
        from_date=start_dt.isoformat(),
        to_date=end_dt.isoformat(),
        granularity=gran,
        bucket_dates=bucket_dates,
        communities=[],
        series=[],
        tracks=[],
        markers=[],
        totals=StrataTotals(mentions=0, entities_introduced=0),
    )

    # Get Neo4j driver
    try:
        driver = get_neo4j()
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("app.routers.graph.strata_neo4j_unavailable", exc)
        return _empty

    # ── Community metadata from Redis artifact ──────────────────────────────
    community_meta: dict[str, dict[str, Any]] = {}
    if redis is not None:
        try:
            raw = redis.get(_COMMUNITY_MAP_REDIS_KEY)
            if raw:
                artifact = json.loads(
                    raw if isinstance(raw, str) else raw.decode("utf-8"),
                )
                for c in artifact.get("communities") or []:
                    cid = str(c["id"])
                    trust_mix_raw = c.get("trust_mix") or {}
                    total_trust = max(1, sum(trust_mix_raw.values()))
                    community_meta[cid] = {
                        "label": c.get("label") or cid,
                        "trust_mix": {
                            k: round(v / total_trust, 4)
                            for k, v in trust_mix_raw.items()
                            if k in _TRUST_STATES
                        },
                    }
        except Exception as exc:  # noqa: BLE001 — community meta is non-fatal
            log_swallowed_error("app.routers.graph.strata_community_meta_read", exc)

    # ── Cypher: per (community_id, entity_type, bucket) mention counts ───────
    start_iso = start_dt.isoformat()
    end_iso = end_dt.isoformat()

    # Aggregate per (entity, DAY) with count(*) instead of returning one row
    # per MENTIONS edge. An entity mentioned N times collapses to <= its
    # active-day count rather than N identical-metadata rows, so the result
    # scales with active-entity-days, not total edges (which exploded memory
    # on a mature corpus). Sums are unchanged — the Python loop adds the
    # per-row ``mentions`` count instead of incrementing by 1.
    mention_cypher = """
        MATCH (a:Artifact)-[m:MENTIONS]->(e:Entity)
        WHERE m.created_at >= $start AND m.created_at <= $end
        WITH e, substring(m.created_at, 0, 10) AS day, count(*) AS mentions
        RETURN
            coalesce(e.community_id, '__null__') AS community_id,
            coalesce(e.entity_type, e.type, 'unknown') AS entity_type,
            coalesce(e.primary_domain, 'other') AS domain,
            day AS ts,
            coalesce(e.trust_state, 'unknown') AS trust_state,
            e.canonical_id AS canonical_id,
            coalesce(e.name, e.canonical_id) AS name,
            coalesce(e.mention_count, 0) AS mention_count,
            e.created_at AS entity_created_at,
            e.primary_domain AS primary_domain,
            mentions AS mentions
    """
    try:
        rows = await asyncio.to_thread(
            _run_strata_cypher, driver, mention_cypher,
            {"start": start_iso, "end": end_iso},
        )
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.routers.graph.strata_mention_query",
            exc,
            context={"granularity": gran},
        )
        return _empty

    if not rows:
        return _empty

    bucket_index: dict[str, int] = {d: i for i, d in enumerate(bucket_dates)}
    n_buckets = len(bucket_dates)

    # Accumulate per-(community, entity_type, domain) buckets
    # key → {"buckets": [int], "unverified_buckets": [int]}
    series_acc: dict[tuple[str, str, str], dict[str, list[int]]] = {}
    # per-community total mentions (for top-8 selection — community lens; amendment #1)
    community_total: dict[str, int] = {}
    # per-entity accumulation for DOI + track buckets
    entity_acc: dict[str, dict[str, Any]] = {}
    # global bucket totals for markers (community lens rollup; stays per amendment #1)
    global_bucket_mentions: list[int] = [0] * n_buckets
    global_bucket_births: list[int] = [0] * n_buckets
    # Tephra Cycle-2: per-domain bucket totals for per-lane markers
    domain_bucket_mentions: dict[str, list[int]] = {}
    domain_bucket_births: dict[str, list[int]] = {}
    # Per-(lane, bucket) top-entity accumulation {(lane_id, bkey): {slug: name}}
    lane_bucket_entities: dict[tuple[str, str], dict[str, str]] = {}
    # Earliest mention ts for data_extent_from
    earliest_ts: str | None = None

    # Track first-seen per entity within the query (to count births)
    entity_first_bucket: dict[str, int] = {}

    for row in rows:
        cid_raw = str(row.get("community_id") or "__null__")
        cid = cid_raw if cid_raw != "__null__" else "other"
        etype = str(row.get("entity_type") or "unknown")
        domain = str(row.get("domain") or "other")
        ts = str(row.get("ts") or "")
        trust = str(row.get("trust_state") or "unknown")
        canon = str(row.get("canonical_id") or "")
        name = str(row.get("name") or canon)
        entity_created_at = str(row.get("entity_created_at") or "")
        primary_domain_val = row.get("primary_domain")
        # Per-(entity, day) mention count from the aggregated query; defaults
        # to 1 so a per-edge row shape (tests / legacy) tallies one each.
        mentions = int(row.get("mentions", 1) or 1)

        bkey = _bucket_key(ts, gran)
        bidx = bucket_index.get(bkey, -1)
        if bidx < 0:
            continue

        # Track earliest ts for data_extent_from hint
        if ts and (earliest_ts is None or ts < earliest_ts):
            earliest_ts = ts

        # Series accumulation — key now includes domain
        sk = (cid, etype, domain)
        if sk not in series_acc:
            series_acc[sk] = {
                "buckets": [0] * n_buckets,
                "unverified_buckets": [0] * n_buckets,
            }
        series_acc[sk]["buckets"][bidx] += mentions
        if trust == "unverified":
            series_acc[sk]["unverified_buckets"][bidx] += mentions

        # Community total (community lens rollup; amendment #1: keep for community lens)
        community_total[cid] = community_total.get(cid, 0) + mentions

        # Global bucket mentions for ingest_burst marker
        global_bucket_mentions[bidx] += mentions

        # Per-domain bucket mentions for per-lane marker attribution
        if domain not in domain_bucket_mentions:
            domain_bucket_mentions[domain] = [0] * n_buckets
        domain_bucket_mentions[domain][bidx] += mentions

        # Per-(lane, bucket) entity accumulation for top_entities
        lb_key = (domain, bkey)
        if lb_key not in lane_bucket_entities:
            lane_bucket_entities[lb_key] = {}
        if canon:
            lane_bucket_entities[lb_key][canon] = name

        # Entity accumulation for tracks
        if canon:
            if canon not in entity_acc:
                entity_acc[canon] = {
                    "name": name,
                    "entity_type": etype,
                    "community_id": cid,
                    "trust_state": trust,
                    "first_seen": entity_created_at,
                    "primary_domain": primary_domain_val,
                    "buckets": [0] * n_buckets,
                    "total": 0,
                    "last_bucket_idx": 0,
                }
            ea = entity_acc[canon]
            ea["buckets"][bidx] += mentions
            ea["total"] += mentions
            ea["last_bucket_idx"] = max(ea["last_bucket_idx"], bidx)

        # Birth tracking: count entity born (created_at) in this bucket
        if canon and canon not in entity_first_bucket:
            birth_bkey = _bucket_key(entity_created_at, gran) if entity_created_at else ""
            birth_bidx = bucket_index.get(birth_bkey, -1)
            entity_first_bucket[canon] = birth_bidx
            if birth_bidx >= 0:
                global_bucket_births[birth_bidx] += 1
                # Also track per-domain births
                if domain not in domain_bucket_births:
                    domain_bucket_births[domain] = [0] * n_buckets
                domain_bucket_births[domain][birth_bidx] += 1

    # ── Top-8 communities (+ "other" rollup) ─────────────────────────────────
    sorted_comms = sorted(community_total.items(), key=lambda kv: kv[1], reverse=True)
    top_comm_ids: list[str] = [cid for cid, _ in sorted_comms[:_STRATA_TOP_COMMUNITIES]]
    top_comm_set = set(top_comm_ids)

    # Build the "other" virtual community for the rest
    other_total = sum(cnt for cid, cnt in sorted_comms[_STRATA_TOP_COMMUNITIES:])
    if other_total > 0 and "other" not in top_comm_set:
        top_comm_ids.append("other")
        top_comm_set.add("other")

    # Remap series rows for communities outside top-8 → "other"
    remapped_series: dict[tuple[str, str, str], dict[str, list[int]]] = {}
    for (cid, etype, domain), acc in series_acc.items():
        target_cid = cid if cid in top_comm_set else "other"
        sk = (target_cid, etype, domain)
        if sk not in remapped_series:
            remapped_series[sk] = {
                "buckets": [0] * n_buckets,
                "unverified_buckets": [0] * n_buckets,
            }
        for i in range(n_buckets):
            remapped_series[sk]["buckets"][i] += acc["buckets"][i]
            remapped_series[sk]["unverified_buckets"][i] += acc["unverified_buckets"][i]

    # ── Build communities list ────────────────────────────────────────────────
    communities_out: list[StrataCommunity] = []
    for cid in top_comm_ids:
        is_other = cid == "other" and cid not in {k for k, _ in sorted_comms[:_STRATA_TOP_COMMUNITIES]}
        meta = community_meta.get(cid, {})
        if meta:
            label = meta["label"]
            trust_mix = meta["trust_mix"]
        else:
            # Degrade: label from top entity in community, trust_mix = zeros
            top_entity_name = next(
                (
                    ea["name"]
                    for ea in sorted(
                        (v for v in entity_acc.values() if v["community_id"] == cid),
                        key=lambda x: x["total"],
                        reverse=True,
                    )
                ),
                cid,
            )
            label = top_entity_name if not is_other else "Other"
            trust_mix = {"verified": 0.0, "partial": 0.0, "unverified": 0.0, "unknown": 0.0}

        c_total = other_total if is_other else community_total.get(cid, 0)
        communities_out.append(StrataCommunity(
            community_id=cid,
            label=label,
            color_slot=_color_slot_from_id(cid),
            trust_mix=trust_mix,
            total_mentions=c_total,
            is_other=is_other,
        ))

    # ── Series rows ───────────────────────────────────────────────────────────
    series_out: list[StrataSeriesRow] = [
        StrataSeriesRow(
            community_id=cid,
            entity_type=etype,
            domain=domain,
            buckets=acc["buckets"],
            unverified_buckets=acc["unverified_buckets"],
        )
        for (cid, etype, domain), acc in remapped_series.items()
        if cid in top_comm_set
    ]

    # ── Top-40 tracks (DOI-sorted) ────────────────────────────────────────────
    scored: list[tuple[float, str]] = []
    for canon, ea in entity_acc.items():
        score = _doi_score(ea["total"], ea["last_bucket_idx"], n_buckets, ea["trust_state"])
        scored.append((score, canon))
    scored.sort(key=lambda t: t[0], reverse=True)
    top_tracks = scored[:_STRATA_TOP_TRACKS]

    tracks_out: list[StrataTrack] = []
    for rank, (_, canon) in enumerate(top_tracks, start=1):
        ea = entity_acc[canon]
        comm_id = ea["community_id"]
        # Remap to "other" if outside top-8
        if comm_id not in top_comm_set:
            comm_id = "other"
        tracks_out.append(StrataTrack(
            canonical_id=canon,
            name=ea["name"],
            entity_type=ea["entity_type"],
            community_id=comm_id,
            trust_state=ea["trust_state"],
            first_seen=ea["first_seen"],
            rank=rank,
            total_mentions=ea["total"],
            buckets=ea["buckets"],
            primary_domain=ea.get("primary_domain"),
        ))

    # ── Markers (global + per-lane) ───────────────────────────────────────────
    # Community lens: global top-8 rollup stays (amendment #1).
    markers_out = _derive_markers(bucket_dates, global_bucket_mentions, global_bucket_births)

    # Per-lane markers for domain lanes (amendment #1: bypassed on community lens;
    # all domain-lens users see lane_id-attributed markers instead of the global rail).
    for d_name, d_mentions in domain_bucket_mentions.items():
        d_births = domain_bucket_births.get(d_name, [0] * n_buckets)
        lane_markers = _derive_markers(bucket_dates, d_mentions, d_births, lane_id=d_name)
        markers_out.extend(lane_markers)

    total_mentions = sum(global_bucket_mentions)
    total_entities = len(entity_acc)

    # ── Tephra Cycle-2: domain taxonomy for lanes[] meta block ────────────────
    lanes_out: list[StrataLaneMeta] = []
    try:
        from app.db.neo4j.taxonomy import get_domain_counts  # noqa: PLC0415

        raw_domains = await asyncio.to_thread(get_domain_counts, driver)
        for d in (raw_domains.get("domains") or []):
            d_name_raw = d.get("name") or ""
            lanes_out.append(StrataLaneMeta(
                lane_id=d_name_raw,
                label=d_name_raw.replace("_", " ").title(),
                icon=str(d.get("icon") or "file"),
                summary_short="",
                summary_full="",
            ))
    except Exception as exc:  # noqa: BLE001 — lanes meta is non-fatal
        log_swallowed_error(
            "app.routers.graph.strata_lanes_meta",
            exc,
        )

    # ── Tephra Cycle-2: KnowledgeLog events ──────────────────────────────────
    events_out: list[StrataEventItem] = []
    ledger_start: str | None = None
    try:
        from app.db.neo4j.knowledge_log import list_log_entries  # noqa: PLC0415

        def _ledger_min_ts() -> str | None:
            with driver.session() as s:
                rec = s.run("MATCH (k:KnowledgeLog) RETURN min(k.ts) AS ts").single()
                return str(rec["ts"]) if rec and rec["ts"] else None

        ledger_start = await asyncio.to_thread(_ledger_min_ts)

        log_rows = await asyncio.to_thread(
            list_log_entries,
            driver,
            since=start_iso,
            limit=_STRATA_MAX_EVENTS_PER_WINDOW,
        )
        # Per-bucket cap: track counts per (lane, bucket)
        per_bucket_event_count: dict[tuple[str, str], int] = {}
        for entry in log_rows:
            slug = str(entry.get("entity_slug") or "")
            # Join to domain via entity's primary_domain — available in entity_acc
            lane_id = ""
            if slug:
                for ea in entity_acc.values():
                    # entity_acc is keyed by canonical_id; slug may match name or id
                    if ea.get("name") == slug or str(ea.get("primary_domain") or "") == slug:
                        lane_id = str(ea.get("primary_domain") or "")
                        break
                # Fallback: try entity_acc directly by canonical_id == slug
                if not lane_id and slug in entity_acc:
                    lane_id = str(entity_acc[slug].get("primary_domain") or "")
            if not lane_id:
                continue  # can't place in a lane; skip

            ts_val = str(entry.get("ts") or "")
            bkey = _bucket_key(ts_val, gran)
            if bkey not in bucket_index:
                continue

            bucket_key_pair = (lane_id, bkey)
            cur = per_bucket_event_count.get(bucket_key_pair, 0)
            if cur >= _STRATA_MAX_EVENTS_PER_BUCKET:
                continue
            per_bucket_event_count[bucket_key_pair] = cur + 1

            summary_raw = str(entry.get("summary") or "")
            events_out.append(StrataEventItem(
                kind=str(entry.get("action") or "refresh"),
                ts=ts_val,
                lane_id=lane_id,
                bucket=bkey,
                entity_slug=slug,
                entity_name=slug,
                summary=summary_raw[:140],
                source_artifact_id=str(entry.get("source_artifact_id") or ""),
            ))
    except Exception as exc:  # noqa: BLE001 — events are non-fatal
        log_swallowed_error(
            "app.routers.graph.strata_knowledge_events",
            exc,
        )

    # ── Tephra Cycle-2: VerificationReport aggregates per (lane, bucket) ─────
    verification_aggs_out: list[StrataVerificationAgg] = []
    try:
        verif_cypher = """
            MATCH (vr:VerificationReport)
            WHERE vr.created_at >= $start AND vr.created_at <= $end
            RETURN
                vr.created_at AS ts,
                coalesce(vr.verified, 0) AS verified,
                coalesce(vr.unverified, 0) AS unverified,
                coalesce(vr.uncertain, 0) AS uncertain,
                coalesce(vr.overall_score, 0.0) AS overall_score
        """
        verif_rows = await asyncio.to_thread(
            _run_strata_cypher,
            driver,
            verif_cypher,
            {"start": start_iso, "end": end_iso},
        )
        # Aggregate per (global bucket) — we don't have lane join on VerificationReport
        # so we attribute to a synthetic "verification" lane in the aggs.
        # Amendment #2: suppress sparse buckets below 3 reports.
        verif_bucket_acc: dict[str, dict[str, Any]] = {}
        for vrow in verif_rows:
            vts = str(vrow.get("ts") or "")
            vbkey = _bucket_key(vts, gran)
            if vbkey not in bucket_index:
                continue
            if vbkey not in verif_bucket_acc:
                verif_bucket_acc[vbkey] = {
                    "count": 0, "verified": 0,
                    "unverified": 0, "uncertain": 0, "score_sum": 0.0,
                }
            acc_v = verif_bucket_acc[vbkey]
            acc_v["count"] += 1
            acc_v["verified"] += int(vrow.get("verified") or 0)
            acc_v["unverified"] += int(vrow.get("unverified") or 0)
            acc_v["uncertain"] += int(vrow.get("uncertain") or 0)
            acc_v["score_sum"] += float(vrow.get("overall_score") or 0.0)

        for vbkey, vacc in verif_bucket_acc.items():
            cnt = vacc["count"]
            if cnt < 3:  # amendment #2: suppress sparse — never render 1-sample signal
                continue
            verification_aggs_out.append(StrataVerificationAgg(
                lane_id="__verification__",
                bucket=vbkey,
                count=cnt,
                verified=vacc["verified"],
                unverified=vacc["unverified"],
                uncertain=vacc["uncertain"],
                overall_score_avg=round(vacc["score_sum"] / cnt, 4) if cnt else 0.0,
            ))
    except Exception as exc:  # noqa: BLE001 — verification aggs are non-fatal
        log_swallowed_error(
            "app.routers.graph.strata_verification_aggs",
            exc,
        )

    # ── Tephra Cycle-2: per-(lane, bucket) top entities (≤3) ─────────────────
    top_entities_out: dict[str, list[StrataTopEntity]] = {}
    for (lb_lane, lb_bkey), ent_map in lane_bucket_entities.items():
        # Take up to 3 entities by insertion order (they're already ordered by mention)
        top3 = [
            StrataTopEntity(name=n, slug=s)
            for s, n in list(ent_map.items())[:3]
        ]
        top_entities_out[f"{lb_lane}:{lb_bkey}"] = top3

    response = StrataResponse(
        from_date=start_dt.isoformat(),
        to_date=end_dt.isoformat(),
        granularity=gran,
        bucket_dates=bucket_dates,
        communities=communities_out,
        series=series_out,
        tracks=tracks_out,
        markers=markers_out,
        totals=StrataTotals(mentions=total_mentions, entities_introduced=total_entities),
        cached=False,
        lanes=lanes_out,
        events=events_out,
        verification_aggs=verification_aggs_out,
        top_entities=top_entities_out,
        data_extent_from=earliest_ts,
        ledger_start_date=ledger_start,
    )

    # Cache 60s
    if redis is not None:
        try:
            redis.setex(
                cache_key,
                _STRATA_TTL_SECONDS,
                json.dumps(response.model_dump()),
            )
        except Exception as exc:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.routers.graph.strata_cache_write",
                exc,
                context={"cache_key": cache_key},
            )

    return response


@router.get("/timeline/track/{canonical_id}", response_model=TrackDetailResponse)
async def get_timeline_track(
    canonical_id: str,
    from_date: str | None = Query(None, alias="from", description="ISO-8601 lower bound"),
    to_date: str | None = Query(None, alias="to", description="ISO-8601 upper bound"),
    bucket: str | None = Query(None, description="Tephra Cycle-2: scope results to a single bucket key"),
) -> TrackDetailResponse:
    """Event-level detail for one entity track (lazy, zoom-triggered).

    Returns up to 500 mention events with per-event co-mentions (cap 20)
    via shared-artifact cypher.  The optional ``bucket=`` param (Tephra
    Cycle-2) scopes results to a single bucket and adds additive fields:
    knowledge_events, new_entities, verification, community_summary.

    Cache: Redis 60s TTL.
    """
    now = datetime.now(tz=timezone.utc)
    end_dt = _parse_iso_or(to_date, now)
    start_dt = _parse_iso_or(from_date, end_dt - timedelta(days=90))
    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="to must be after from")

    start_iso = start_dt.isoformat()
    end_iso = end_dt.isoformat()
    cache_key = _track_cache_key(canonical_id, start_iso, end_iso, bucket or "")
    redis = get_redis()

    # Cache fast-path
    try:
        if redis is not None:
            cached_raw = redis.get(cache_key)
            if cached_raw is not None:
                payload = json.loads(
                    cached_raw.decode() if isinstance(cached_raw, bytes) else cached_raw,
                )
                payload["cached"] = True
                return TrackDetailResponse(**payload)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.routers.graph.track_cache_read",
            exc,
            context={"cache_key": cache_key},
        )
        redis = get_redis()

    _empty_track = TrackDetailResponse(
        canonical_id=canonical_id,
        name=canonical_id,
        events=[],
    )

    try:
        driver = get_neo4j()
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("app.routers.graph.track_neo4j_unavailable", exc)
        return _empty_track

    # Fetch mention events + co-mentioned entities (shared artifact, capped 20)
    cypher = """
        MATCH (a:Artifact)-[m:MENTIONS]->(e:Entity {canonical_id: $canonical_id})
        WHERE m.created_at >= $start AND m.created_at <= $end
        WITH a, m
        ORDER BY m.created_at DESC
        LIMIT $max_events
        OPTIONAL MATCH (a)-[:MENTIONS]->(co:Entity)
        WHERE co.canonical_id <> $canonical_id
        WITH a, m,
             collect(DISTINCT {canonical_id: co.canonical_id, name: coalesce(co.name, co.canonical_id)})[..$co_cap]
             AS co_mentioned
        RETURN
            m.created_at AS ts,
            a.artifact_id AS artifact_id,
            coalesce(a.filename, a.artifact_id) AS artifact_filename,
            coalesce(m.confidence, 1.0) AS confidence,
            coalesce(a.summary, '') AS summary,
            co_mentioned
        ORDER BY ts DESC
    """
    try:
        rows = await asyncio.to_thread(
            _run_strata_cypher,
            driver,
            cypher,
            {
                "canonical_id": canonical_id,
                "start": start_iso,
                "end": end_iso,
                "max_events": _TRACK_MAX_EVENTS,
                "co_cap": _TRACK_CO_MENTIONED_CAP,
            },
        )
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.routers.graph.track_cypher",
            exc,
            context={"canonical_id": canonical_id},
        )
        return _empty_track

    if not rows:
        return _empty_track

    # Resolve entity name from first row (the focal entity is not in the RETURN)
    # We pull it from a separate lightweight query if rows exist.
    entity_name = canonical_id
    try:
        name_rows = await asyncio.to_thread(
            _run_strata_cypher,
            driver,
            "MATCH (e:Entity {canonical_id: $id}) RETURN coalesce(e.name, e.canonical_id) AS name LIMIT 1",
            {"id": canonical_id},
        )
        if name_rows:
            entity_name = str(name_rows[0].get("name") or canonical_id)
    except Exception as exc:  # noqa: BLE001 — name lookup is non-fatal
        log_swallowed_error("app.routers.graph.track_name_lookup", exc)

    events: list[TrackEvent] = []
    for row in rows:
        co_raw = row.get("co_mentioned") or []
        co_list: list[dict[str, str]] = []
        for co in co_raw:
            if isinstance(co, dict) and co.get("canonical_id"):
                co_list.append({
                    "canonical_id": str(co["canonical_id"]),
                    "name": str(co.get("name") or co["canonical_id"]),
                })
        events.append(TrackEvent(
            ts=str(row.get("ts") or ""),
            artifact_id=str(row.get("artifact_id") or ""),
            artifact_filename=str(row.get("artifact_filename") or ""),
            confidence=float(row.get("confidence") or 1.0),
            summary=str(row.get("summary") or "")[:200],
            co_mentioned=co_list,
        ))

    # ── Tephra Cycle-2 additive fields ────────────────────────────────────────
    knowledge_events_out: list[TrackKnowledgeEvent] = []
    new_entities_out: list[TrackNewEntity] = []
    verification_out = TrackVerification()
    community_summary_out = ""

    # KnowledgeLog events for this entity
    try:
        from app.db.neo4j.knowledge_log import list_log_entries  # noqa: PLC0415

        ke_rows = await asyncio.to_thread(
            list_log_entries,
            driver,
            entity_slug=canonical_id,
            since=start_iso,
            limit=100,
        )
        for ke in ke_rows:
            knowledge_events_out.append(TrackKnowledgeEvent(
                kind=str(ke.get("action") or "refresh"),
                ts=str(ke.get("ts") or ""),
                entity_slug=str(ke.get("entity_slug") or ""),
                summary=str(ke.get("summary") or "")[:140],
                source_artifact_id=str(ke.get("source_artifact_id") or ""),
            ))
    except Exception as exc:  # noqa: BLE001 — knowledge events non-fatal
        log_swallowed_error(
            "app.routers.graph.track_knowledge_events",
            exc,
            context={"canonical_id": canonical_id},
        )

    # New entities born in the window (co-domain births)
    try:
        birth_cypher = """
            MATCH (e:Entity)
            WHERE e.created_at >= $start AND e.created_at <= $end
            MATCH (a:Artifact)-[:MENTIONS]->(focal:Entity {canonical_id: $canonical_id})
            WHERE (a)-[:MENTIONS]->(e)
            RETURN DISTINCT
                coalesce(e.name, e.canonical_id) AS name,
                e.canonical_id AS slug,
                e.created_at AS created_at
            ORDER BY e.created_at DESC
            LIMIT 20
        """
        birth_rows = await asyncio.to_thread(
            _run_strata_cypher,
            driver,
            birth_cypher,
            {"canonical_id": canonical_id, "start": start_iso, "end": end_iso},
        )
        for br in birth_rows:
            new_entities_out.append(TrackNewEntity(
                name=str(br.get("name") or ""),
                slug=str(br.get("slug") or ""),
                created_at=str(br.get("created_at") or ""),
            ))
    except Exception as exc:  # noqa: BLE001 — new_entities non-fatal
        log_swallowed_error(
            "app.routers.graph.track_new_entities",
            exc,
            context={"canonical_id": canonical_id},
        )

    # VerificationReport aggregates for this entity's artifacts
    try:
        verif_track_cypher = """
            MATCH (e:Entity {canonical_id: $canonical_id})<-[:MENTIONS]-(a:Artifact)
            MATCH (vr:VerificationReport)-[:EXTRACTED_FROM]->(a)
            WHERE vr.created_at >= $start AND vr.created_at <= $end
            RETURN
                count(vr) AS reports,
                sum(coalesce(vr.verified, 0)) AS verified,
                sum(coalesce(vr.unverified, 0)) AS unverified,
                sum(coalesce(vr.uncertain, 0)) AS uncertain,
                avg(coalesce(vr.overall_score, 0.0)) AS score_avg
        """
        vt_rows = await asyncio.to_thread(
            _run_strata_cypher,
            driver,
            verif_track_cypher,
            {"canonical_id": canonical_id, "start": start_iso, "end": end_iso},
        )
        if vt_rows:
            vtr = vt_rows[0]
            verification_out = TrackVerification(
                reports=int(vtr.get("reports") or 0),
                verified=int(vtr.get("verified") or 0),
                unverified=int(vtr.get("unverified") or 0),
                uncertain=int(vtr.get("uncertain") or 0),
                overall_score_avg=round(float(vtr.get("score_avg") or 0.0), 4),
            )
    except Exception as exc:  # noqa: BLE001 — verification non-fatal
        log_swallowed_error(
            "app.routers.graph.track_verification",
            exc,
            context={"canonical_id": canonical_id},
        )

    # Community summary for community lens
    try:
        comm_cypher = """
            MATCH (e:Entity {canonical_id: $canonical_id})
            MATCH (c:Community {id: toString(e.community_id)})
            WHERE c.summary IS NOT NULL
            RETURN c.summary AS summary LIMIT 1
        """
        comm_rows = await asyncio.to_thread(
            _run_strata_cypher,
            driver,
            comm_cypher,
            {"canonical_id": canonical_id},
        )
        if comm_rows:
            community_summary_out = str(comm_rows[0].get("summary") or "")
    except Exception as exc:  # noqa: BLE001 — community summary non-fatal
        log_swallowed_error(
            "app.routers.graph.track_community_summary",
            exc,
            context={"canonical_id": canonical_id},
        )

    response = TrackDetailResponse(
        canonical_id=canonical_id,
        name=entity_name,
        events=events,
        cached=False,
        knowledge_events=knowledge_events_out,
        new_entities=new_entities_out,
        verification=verification_out,
        community_summary=community_summary_out,
    )

    if redis is not None:
        try:
            redis.setex(
                cache_key,
                _STRATA_TTL_SECONDS,
                json.dumps(response.model_dump()),
            )
        except Exception as exc:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.routers.graph.track_cache_write",
                exc,
                context={"cache_key": cache_key},
            )

    return response


# ---------------------------------------------------------------------------
# Domain aggregate endpoint (Cycle 1 domain backbone)
# ---------------------------------------------------------------------------


class DomainSubCategory(BaseModel):
    """One sub-category row in the /graph/domains response."""
    name: str
    artifact_count: int
    entity_count: int


class DomainSummary(BaseModel):
    """Per-domain aggregate for /graph/domains."""
    name: str
    icon: str | None = None
    description: str | None = None
    in_taxonomy: bool = False
    artifact_count: int = 0
    entity_count: int = 0
    # Corpus-level salience mass (Slice 6.2) — the response is ordered by this;
    # 0.0 until DeriveDomainsJob runs (degrades to entity_count ordering).
    salience: float = 0.0
    sub_categories: list[DomainSubCategory] = []


class DomainsResponse(BaseModel):
    """Shape returned by GET /graph/domains.

    ``derived_at: null`` means DeriveDomainsJob has never run — every
    frontend surface keys its degraded state on this signal.
    """
    domains: list[DomainSummary]
    uncategorized_entities: int
    derived_at: str | None


@router.get("/domains", response_model=DomainsResponse)
async def get_domains() -> DomainsResponse:
    """Per-domain entity/artifact counts — the taxonomy-aware spine endpoint.

    Sorted by entity_count desc. ``derived_at: null`` signals that the
    DeriveDomainsJob has never run; frontend surfaces use this to render
    byte-identical degraded states rather than erroring.

    No Redis cache in v1 — two indexed aggregates run in single-digit ms
    at live scale.  Add one if it ever shows in traces.
    """
    _empty = DomainsResponse(domains=[], uncategorized_entities=0, derived_at=None)

    try:
        driver = get_neo4j()
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("app.routers.graph.domains_neo4j_unavailable", exc)
        return _empty

    if driver is None:
        return _empty

    from app.db.neo4j.taxonomy import get_domain_counts  # noqa: PLC0415

    try:
        raw = await asyncio.to_thread(get_domain_counts, driver)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("app.routers.graph.domains_query", exc)
        return _empty

    domains_out: list[DomainSummary] = []
    for d in raw.get("domains") or []:
        sub_cats = [
            DomainSubCategory(
                name=sc["name"],
                artifact_count=int(sc.get("artifact_count") or 0),
                entity_count=int(sc.get("entity_count") or 0),
            )
            for sc in (d.get("sub_categories") or [])
        ]
        domains_out.append(DomainSummary(
            name=d["name"],
            icon=d.get("icon"),
            description=d.get("description"),
            in_taxonomy=bool(d.get("in_taxonomy")),
            artifact_count=int(d.get("artifact_count") or 0),
            entity_count=int(d.get("entity_count") or 0),
            salience=float(d.get("salience") or 0.0),
            sub_categories=sub_cats,
        ))

    return DomainsResponse(
        domains=domains_out,
        uncategorized_entities=int(raw.get("uncategorized_entities") or 0),
        derived_at=raw.get("derived_at"),
    )


# ---------------------------------------------------------------------------
# STRATA Decomposition endpoint (Cycle 4)
# ---------------------------------------------------------------------------

_DECOMPOSITION_CACHE_KEY = "cerid:graph:emb3d:v3:decomposition"
_DECOMPOSITION_TTL_SECONDS = int(
    os.getenv("GRAPH_DECOMPOSITION_CACHE_TTL", str(86400))
)


class CommunityHub(BaseModel):
    """Top-degree entity within a community, ordered by degree descending."""

    id: str
    name: str
    degree: int


class DecompositionL0Community(BaseModel):
    """L0 community node in the decomposition tree (contract: L0Community)."""

    id: str
    mode_domain: str
    purity: float
    size: int
    label: str | None = None
    top_hubs: list[CommunityHub] = []


class DecompositionL0RollupBucket(BaseModel):
    """Rollup bucket for L0 communities of size < 4 (contract: L0RollupBucket)."""

    kind: str = "rollup"
    community_count: int
    entity_count: int


class DecompositionL1Community(BaseModel):
    """L1 community node in the decomposition tree (contract: L1Community)."""

    id: str
    mode_domain: str
    purity: float
    size: int
    label: str | None = None
    top_hubs: list[CommunityHub] = []
    children: list[DecompositionL0Community | DecompositionL0RollupBucket] = []


class DecompositionSubcategory(BaseModel):
    """Subcategory tier node (contract: SubCategoryNode)."""

    id: str
    label: str
    entity_count: int
    children: list[DecompositionL1Community] = []


class DecompositionUnclustered(BaseModel):
    """Per-domain unclustered bucket (contract: UnclusteredBucket)."""

    count: int


class DecompositionDomain(BaseModel):
    """Domain node in the decomposition tree (contract: DomainNode)."""

    id: str
    label: str
    entity_count: int
    unclustered: DecompositionUnclustered
    subcategories: list[DecompositionSubcategory] | None = None
    communities: list[DecompositionL1Community] | None = None


class DecompositionEntityLeaf(BaseModel):
    """Entity leaf returned by ?community= param (contract: EntityLeaf)."""

    id: str
    name: str
    type: str
    trust_state: str
    path: list[str]  # [domain, sub?, l1, l0]


class DecompositionResponse(BaseModel):
    """Shape returned by GET /graph/decomposition (contract: DecompositionPayload).

    ``no_communities_computed: true`` means Leiden has never run — the
    icicle should degrade to an honest Domain→Entity two-tier (A3).
    """

    domains: list[DecompositionDomain]
    parent_map: dict[str, str]
    uncategorized_count: int
    no_communities_computed: bool
    computed_at: str | None
    cached: bool = False


class DecompositionCommunityLeafResponse(BaseModel):
    """Shape returned by GET /graph/decomposition?community=<id>."""

    community_id: str
    entities: list[DecompositionEntityLeaf]
    cached: bool = False


def _assemble_l1(l1: dict[str, Any]) -> DecompositionL1Community:
    """Build a DecompositionL1Community from a raw decomposition dict."""
    children: list[DecompositionL0Community | DecompositionL0RollupBucket] = []
    for child in l1.get("children") or []:
        if child.get("kind") == "rollup":
            children.append(DecompositionL0RollupBucket(
                kind="rollup",
                community_count=int(child.get("community_count") or 0),
                entity_count=int(child.get("entity_count") or 0),
            ))
        else:
            children.append(DecompositionL0Community(
                id=child["id"],
                size=int(child.get("size") or 0),
                label=child.get("label") or None,
                mode_domain=child.get("mode_domain") or "",
                purity=float(child.get("purity") or 1.0),
                top_hubs=[CommunityHub(**h) for h in (child.get("top_hubs") or [])],
            ))
    return DecompositionL1Community(
        id=l1["id"],
        size=int(l1.get("size") or 0),
        label=l1.get("label") or None,
        mode_domain=l1.get("mode_domain") or "",
        purity=float(l1.get("purity") or 1.0),
        top_hubs=[CommunityHub(**h) for h in (l1.get("top_hubs") or [])],
        children=children,
    )


@router.get("/decomposition", response_model=DecompositionResponse | DecompositionCommunityLeafResponse)
async def get_graph_decomposition(
    community: str | None = Query(
        default=None,
        description="When provided, return entity leaves for this L0 community "
                    "each carrying path:[domain, sub?, l1, l0]. Omit for the full tree.",
    ),
) -> DecompositionResponse | DecompositionCommunityLeafResponse:
    """STRATA decomposition tree — the Atlas icicle data source.

    Without ?community=: returns the full tier tree (11 domains, conditional
    subcategory groups from e.primary_subcategory, 262 L1 + 503 L0 communities
    with sizes/labels/mode-domain/purity, derived L0→L1 parent map, per-domain
    unclustered counts, size<4 rollup buckets, no_communities_computed flag).

    With ?community=<id>: returns entity leaves for that L0 community, each
    carrying path:[domain, sub?, l1, l0] for the search-palette path walk.

    Cache: Redis SETEX 24h under ``cerid:graph:emb3d:v3:decomposition``
    which matches the ``cerid:graph:emb3d:*`` bust pattern, so the nightly
    compute_umap_3d run invalidates it automatically.

    no_communities_computed=true means Leiden has never run — the client
    should degrade to an honest Domain→Entity two-tier with the notice
    "Clusters appear after the nightly analysis runs" (A3).

    Use when: the Atlas icicle panel needs to render the knowledge-base
    hierarchy for exploration.

    Returns (tree): no_communities_computed, domains (with l1/l0 community
    trees + rollup buckets + unclustered counts), l0_to_l1 parent map,
    unclustered_by_domain, derived_at, cached.

    Returns (leaf): community_id, entities (id/name/entity_type/trust_state/
    mention_count/primary_domain/path), cached.
    """
    redis = get_redis()

    _empty_tree = DecompositionResponse(
        no_communities_computed=True,
        domains=[],
        parent_map={},
        uncategorized_count=0,
        computed_at=None,
    )

    try:
        driver = get_neo4j()
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("app.routers.graph.decomposition_neo4j_unavailable", exc)
        return _empty_tree

    if driver is None:
        return _empty_tree

    from app.db.neo4j.decomposition import (  # noqa: PLC0415
        get_community_entities,
        get_decomposition_tree,
    )

    # --- ?community= leaf path -------------------------------------------------
    if community is not None:
        leaf_cache_key = (
            f"cerid:graph:emb3d:v3:decomposition:leaf:{community}"
        )
        if redis:
            try:
                cached_raw = redis.get(leaf_cache_key)
                if cached_raw:
                    payload = json.loads(
                        cached_raw if isinstance(cached_raw, str)
                        else cached_raw.decode("utf-8"),
                    )
                    payload["cached"] = True
                    return DecompositionCommunityLeafResponse(**payload)
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                logger.info("graph.decomposition.leaf_cache_miss community=%s: %s", community, exc)

        try:
            entities_raw = await asyncio.to_thread(get_community_entities, driver, community)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "app.routers.graph.decomposition_leaf_query",
                exc,
                context={"community": community},
            )
            raise HTTPException(status_code=500, detail="Decomposition leaf query failed.")

        if entities_raw is None:
            raise HTTPException(status_code=404, detail=f"Community '{community}' not found.")

        entity_leaves = [
            DecompositionEntityLeaf(
                id=e["id"],
                name=e.get("name") or e["id"],
                type=e.get("type") or "OTHER",
                trust_state=e.get("trust_state") or "unknown",
                path=e.get("path") or [],
            )
            for e in entities_raw
        ]
        leaf_response = DecompositionCommunityLeafResponse(
            community_id=community,
            entities=entity_leaves,
            cached=False,
        )

        if redis:
            try:
                redis.set(
                    leaf_cache_key,
                    leaf_response.model_dump_json(),
                    ex=_DECOMPOSITION_TTL_SECONDS,
                )
            except (OSError, ValueError) as exc:
                logger.info("graph.decomposition.leaf_cache_write_failed: %s", exc)

        return leaf_response

    # --- Full tree path --------------------------------------------------------
    if redis:
        try:
            cached_raw = redis.get(_DECOMPOSITION_CACHE_KEY)
            if cached_raw:
                payload = json.loads(
                    cached_raw if isinstance(cached_raw, str)
                    else cached_raw.decode("utf-8"),
                )
                payload["cached"] = True
                return DecompositionResponse(**payload)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.info("graph.decomposition.cache_read_miss: %s", exc)

    try:
        raw = await asyncio.to_thread(get_decomposition_tree, driver)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("app.routers.graph.decomposition_tree_query", exc)
        return _empty_tree

    # Assemble Pydantic models from raw dict (field names mirror the TS contract)
    domain_nodes: list[DecompositionDomain] = []
    for d in raw.get("domains") or []:
        sub_nodes: list[DecompositionSubcategory] | None = None
        if d.get("subcategories") is not None:
            sub_nodes = [
                DecompositionSubcategory(
                    id=sc["id"],
                    label=sc.get("label") or sc["id"],
                    entity_count=int(sc.get("entity_count") or 0),
                    children=[
                        _assemble_l1(l1) for l1 in (sc.get("children") or [])
                    ],
                )
                for sc in d["subcategories"]
            ]

        l1_list: list[DecompositionL1Community] = [
            _assemble_l1(l1) for l1 in (d.get("communities") or [])
        ]

        domain_nodes.append(DecompositionDomain(
            id=d["id"],
            label=d.get("label") or d["id"],
            entity_count=int(d.get("entity_count") or 0),
            unclustered=DecompositionUnclustered(
                count=int((d.get("unclustered") or {}).get("count") or 0),
            ),
            subcategories=sub_nodes,
            communities=l1_list if sub_nodes is None else None,
        ))

    response = DecompositionResponse(
        no_communities_computed=bool(raw.get("no_communities_computed", False)),
        domains=domain_nodes,
        parent_map=raw.get("parent_map") or {},
        uncategorized_count=int(raw.get("uncategorized_count") or 0),
        computed_at=raw.get("computed_at"),
        cached=False,
    )

    if redis:
        try:
            redis.set(
                _DECOMPOSITION_CACHE_KEY,
                response.model_dump_json(),
                ex=_DECOMPOSITION_TTL_SECONDS,
            )
        except (OSError, ValueError) as exc:
            logger.info("graph.decomposition.cache_write_failed: %s", exc)

    return response
