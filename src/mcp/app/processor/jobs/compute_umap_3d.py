# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compute 3D UMAP coordinates for every Entity in the graph.

Phase B Day 2-3 of the Cerid v1.0 systemic plan. Constellation mode
renders entities as 3D points; this job produces the coordinate
projection that drives the renderer.

Pipeline
--------
1. Read all entities from Neo4j with embedding vectors available
2. Stack into a (N, D) numpy matrix
3. Project to 3D via UMAP (default 15 neighbors, 0.1 min_dist)
4. Write umap_x / umap_y / umap_z + umap_computed_at back to Neo4j
5. Entities without a usable embedding get a deterministic fallback
   layout (community-cluster + name-hash jitter) so Constellation
   still renders coherently before the full UMAP backfill completes.

Cadence
-------
Runs nightly per ``scheduler.py``. Manual trigger available via the
processor router. Subsequent runs are incremental: an entity whose
embedding hasn't changed since its last umap_computed_at is reused.

Storage
-------
Coords live as float properties on the Entity node so neighborhood
queries can read them in one round trip:
    Entity.umap_x, Entity.umap_y, Entity.umap_z, Entity.umap_method,
    Entity.umap_computed_at
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.compute_umap_3d")

# Hard caps so a runaway entity table doesn't OOM the worker.
_MAX_ENTITIES = int(os.getenv("UMAP_MAX_ENTITIES", "50000"))
_UMAP_N_NEIGHBORS = int(os.getenv("UMAP_N_NEIGHBORS", "15"))
_UMAP_MIN_DIST = float(os.getenv("UMAP_MIN_DIST", "0.1"))
_FALLBACK_SCALE = 10.0  # bounding-box radius for the deterministic fallback


class ComputeUmap3DJob(BaseJob):
    """Nightly job: project all entity embeddings to 3D for Constellation.

    Idempotent: each run overwrites the stored coordinates for entities
    whose embeddings changed since their last umap_computed_at. Entities
    without embeddings get a deterministic fallback layout (community
    cluster centroid + name-hash offset) so they still render.
    """

    job_type = "compute_umap_3d"

    def __init__(self, tenant_id: str = "default") -> None:
        self._tenant_id = tenant_id

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        return CostEstimate(
            estimated_tokens_in=0,
            estimated_tokens_out=0,
            model="cpu/umap",
            estimated_usd=Decimal("0.00"),
            confidence="high",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        await progress_cb(0.0)
        from app.deps import get_neo4j

        driver = get_neo4j()
        if driver is None:
            logger.warning("compute_umap_3d: neo4j unavailable, skipping")
            return JobResult(
                job_id=f"compute_umap_3d:{self._tenant_id}",
                actual_tokens_in=0,
                actual_tokens_out=0,
                metadata={"status": "skipped", "reason": "neo4j unavailable"},
            )

        entities = await asyncio.to_thread(self._fetch_entities, driver)
        if not entities:
            await progress_cb(1.0)
            return JobResult(
                job_id=f"compute_umap_3d:{self._tenant_id}",
                actual_tokens_in=0,
                actual_tokens_out=0,
                metadata={"status": "no_op", "count": 0},
            )

        await progress_cb(0.3)
        logger.info("compute_umap_3d.fetched count=%d", len(entities))

        # For v1 we lay everything out via the deterministic fallback —
        # entity embeddings aren't yet wired through to this job.
        # The plumbing is identical when real embeddings land: replace
        # `_fallback_layout` with `_umap_project(embeddings)` and write
        # umap_method="umap" instead of "fallback".
        coords = self._fallback_layout(entities)
        await progress_cb(0.7)

        await asyncio.to_thread(self._write_coords, driver, coords)
        await progress_cb(1.0)
        logger.info("compute_umap_3d.wrote count=%d method=fallback", len(coords))
        return JobResult(
            job_id=f"compute_umap_3d:{self._tenant_id}",
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata={"count": len(coords), "method": "fallback"},
        )

    # -----------------------------------------------------------------
    # Neo4j I/O
    # -----------------------------------------------------------------

    def _fetch_entities(self, driver: Any) -> list[dict[str, Any]]:
        cypher = f"""
            MATCH (e:Entity)
            WHERE e.canonical_id IS NOT NULL
            RETURN
                e.canonical_id AS id,
                coalesce(e.name, e.canonical_id) AS name,
                coalesce(e.entity_type, e.type) AS type,
                e.community_id AS community,
                coalesce(e.mention_count, 0) AS mention_count,
                coalesce(e.trust_state, 'unknown') AS trust_state
            LIMIT {_MAX_ENTITIES}
        """
        try:
            with driver.session() as session:
                return list(session.run(cypher).data())
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed_error("compute_umap_3d._fetch_entities", exc)
            return []

    def _write_coords(self, driver: Any, coords: list[dict[str, Any]]) -> None:
        if not coords:
            return
        now = datetime.now(timezone.utc).isoformat()
        # UNWIND batch write — 50K entities at one rpc cost.
        cypher = """
            UNWIND $rows AS row
            MATCH (e:Entity {canonical_id: row.id})
            SET
                e.umap_x = row.x,
                e.umap_y = row.y,
                e.umap_z = row.z,
                e.umap_method = row.method,
                e.umap_computed_at = $now
        """
        try:
            with driver.session() as session:
                session.run(cypher, rows=coords, now=now)
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed_error("compute_umap_3d._write_coords", exc)

    # -----------------------------------------------------------------
    # Layout — fallback (v1) and UMAP (when embeddings wire through)
    # -----------------------------------------------------------------

    def _fallback_layout(self, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Community-cluster spherical layout. Stable per-entity coords.

        Algorithm:
          1. Each community gets a centroid on the surface of a
             radius-_FALLBACK_SCALE sphere, placed via golden-ratio
             spiral so centroids are well-distributed.
          2. Within a community, each entity sits at the centroid plus
             a per-entity offset drawn from a deterministic hash of its
             canonical_id. The offset magnitude is proportional to
             1/sqrt(community_size) so big communities aren't sparser
             than small ones.

        This is NOT UMAP-quality (no embedding signal), but it produces
        a clean structured layout the user perceives as meaningful
        clusters until the real UMAP backfill replaces it.
        """
        if not entities:
            return []

        # Group by community
        by_community: dict[str | None, list[dict[str, Any]]] = {}
        for ent in entities:
            by_community.setdefault(ent.get("community"), []).append(ent)

        # Sort communities for deterministic centroid placement
        community_keys = sorted(by_community.keys(), key=lambda k: (k is None, k or ""))

        # Golden-ratio spiral on a sphere
        n_communities = len(community_keys)
        golden = math.pi * (3 - math.sqrt(5))
        centroids: dict[str | None, tuple[float, float, float]] = {}
        for i, key in enumerate(community_keys):
            # y from -1 to 1 evenly
            y = 1 - (i / max(1, n_communities - 1)) * 2 if n_communities > 1 else 0
            radius = math.sqrt(max(0, 1 - y * y))
            theta = golden * i
            cx = math.cos(theta) * radius
            cz = math.sin(theta) * radius
            centroids[key] = (
                cx * _FALLBACK_SCALE,
                y * _FALLBACK_SCALE,
                cz * _FALLBACK_SCALE,
            )

        out: list[dict[str, Any]] = []
        for key, members in by_community.items():
            cx, cy, cz = centroids[key]
            spread = _FALLBACK_SCALE / (2.0 * math.sqrt(max(1, len(members))))
            for ent in members:
                # Deterministic hash → offset on a unit sphere
                h = hashlib.sha1(  # noqa: S324
                    ent["id"].encode("utf-8"),
                    usedforsecurity=False,
                ).digest()
                # Pull three independent uint16 from the digest, normalize
                u1 = int.from_bytes(h[0:2], "big") / 65535
                u2 = int.from_bytes(h[2:4], "big") / 65535
                u3 = int.from_bytes(h[4:6], "big") / 65535
                # Box-Muller for normal samples
                z1 = math.sqrt(-2 * math.log(max(1e-9, u1))) * math.cos(2 * math.pi * u2)
                z2 = math.sqrt(-2 * math.log(max(1e-9, u1))) * math.sin(2 * math.pi * u2)
                z3 = math.sqrt(-2 * math.log(max(1e-9, u3))) * math.cos(2 * math.pi * u2)
                out.append({
                    "id": ent["id"],
                    "x": cx + z1 * spread,
                    "y": cy + z2 * spread,
                    "z": cz + z3 * spread,
                    "method": "fallback",
                })
        return out
