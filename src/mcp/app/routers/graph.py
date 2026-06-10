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
import os
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

    # Cypher: native variable-length expansion (Neo4j 5.x). Degree cap uses the
    # COUNT {} subquery form — size() over a relationship pattern was removed in
    # Neo4j 5.x. Filter narrows by entity type if specified. We also pull
    # edge attestation + contradiction flags for the visual encoding.
    type_filter = ""
    if filter:
        type_filter = "AND (e.type = $filter OR n.type = $filter)"

    cypher = f"""
        MATCH (n:Entity {{canonical_id: $entity}})
        OPTIONAL MATCH path = (n)-[*1..{hops}]-(e:Entity)
        WHERE e.canonical_id IS NOT NULL {type_filter}
          AND COUNT {{ (e)--() }} < $max_degree
        WITH n, collect(DISTINCT e) AS related, collect(DISTINCT relationships(path)) AS rel_lists
        UNWIND ([n] + related) AS node
        OPTIONAL MATCH (node)-[r]-(other:Entity)
        WHERE other IN ([n] + related)
        WITH DISTINCT
            node,
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
            edges_for_node AS edges
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

    # Truncation signal: degree cap was hit somewhere in the expansion
    truncated = any(
        n.mention_count > _MAX_DEGREE for n in nodes
    )

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

    if entity:
        # Per-entity timeline: count mentions FROM Artifact TO this entity
        cypher = (
            "MATCH (a:Artifact)-[m:MENTIONS]->(e:Entity {canonical_id: $entity}) "
            "WHERE m.created_at >= $start AND m.created_at <= $end "
            "RETURN m.created_at AS ts, false AS is_birth "
            "UNION ALL "
            "MATCH (e:Entity {canonical_id: $entity}) "
            "WHERE e.created_at >= $start AND e.created_at <= $end "
            "RETURN e.created_at AS ts, true AS is_birth"
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
            "RETURN m.created_at AS ts, false AS is_birth "
            "UNION ALL "
            "MATCH (e:Entity) "
            "WHERE e.created_at >= $start AND e.created_at <= $end "
            "RETURN e.created_at AS ts, true AS is_birth"
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

    # Bucket
    by_bucket: dict[str, dict[str, int]] = {}
    for row in rows:
        ts = row.get("ts")
        if not ts:
            continue
        key = _bucket_key(str(ts), gran)
        if not key:
            continue
        bucket = by_bucket.setdefault(key, {"mention_count": 0, "entities_introduced": 0})
        if row.get("is_birth"):
            bucket["entities_introduced"] += 1
        else:
            bucket["mention_count"] += 1

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
    # v2 suffix: payload gained `links` — versioning the key prevents serving
    # a cached pre-links payload. The shared bust pattern cerid:graph:emb3d:*
    # still matches.
    if filter_str or entities_csv:
        h = hashlib.sha1(  # noqa: S324
            f"{filter_str}|{entities_csv}".encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()[:12]
        return f"cerid:graph:emb3d:v2:{h}"
    return "cerid:graph:emb3d:v2:all"


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
            e.umap_computed_at AS computed_at
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
_GRAPH_MAP_CACHE_KEY = "cerid:graph:emb3d:v2:map"


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


@router.get("/map", response_model=GraphMapResponse)
async def get_graph_map() -> GraphMapResponse:
    """Full cartographic map payload for Constellation.

    Bundles the 2D-projected entity positions, CO_MENTIONED edge links, and
    the precomputed community hull/anchor/trust-mix artifacts from the
    compute_umap_3d nightly job into one cached response.

    Cache: Redis SETEX ``GRAPH_EMBEDDINGS_3D_CACHE_TTL`` (default 24h).
    The key ``cerid:graph:emb3d:v2:map`` matches the ``cerid:graph:emb3d:*``
    bust pattern so a job recompute invalidates this endpoint automatically.

    Community artifacts degrade to ``communities=[]`` when the nightly job
    has not yet written the Redis artifact — the entity+link payload is
    always returned.
    """
    redis = get_redis()

    # Cache fast-path.
    if redis:
        try:
            cached_raw = redis.get(_GRAPH_MAP_CACHE_KEY)
            if cached_raw:
                payload = json.loads(
                    cached_raw if isinstance(cached_raw, str)
                    else cached_raw.decode("utf-8"),
                )
                payload["cached"] = True
                return GraphMapResponse(**payload)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            # silent-catch-allowed: cache miss is non-fatal — re-fetch.
            logger.info("graph.map.cache_read_miss: %s", exc)

    # Entity positions and links.
    rows = await _query_embeddings_3d(None, None)

    computed_at_values = [str(r["computed_at"]) for r in rows if r.get("computed_at")]
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
        )
        for r in rows
    ]

    links = await _query_embeddings_3d_links([r["id"] for r in rows])

    # Community artifacts — degrade gracefully if missing.
    communities: list[MapCommunity] = []
    silhouette: float | None = None
    if redis:
        try:
            raw = redis.get(_COMMUNITY_MAP_REDIS_KEY)
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
    )

    if redis:
        try:
            redis.set(
                _GRAPH_MAP_CACHE_KEY,
                response.model_dump_json(),
                ex=_EMBEDDINGS_3D_TTL_SECONDS,
            )
        except (OSError, ValueError) as exc:
            # silent-catch-allowed: cache-write failure non-fatal.
            logger.info("graph.map.cache_write_failed: %s", exc)

    return response
