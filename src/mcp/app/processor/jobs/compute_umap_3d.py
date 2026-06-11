# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compute 2D cartographic layout + community artifacts for Constellation.

Phase 0 of the Cartographer knowledge-graph redesign.  The job produces
three outputs written to Neo4j + Redis:

  1. Entity positions (x, y, z) — 2D ForceAtlas2-style LinLog layout with
     degree-weighted repulsion, noverlap removal, and Procrustes stability
     alignment.  ``z`` carries optional community-relief depth so the R3F
     3D toggle shares the same x/y mental map.

  2. Community map artifacts stored in Redis key
     ``cerid:graph:map:communities`` as JSON — hulls, anchors, trust mixes,
     and a silhouette quality metric.

  3. Cache bust of ``cerid:graph:emb3d:*`` so Constellation picks up new
     coords on its next poll.

Pipeline
--------
1. Read all entities from Neo4j (canonical_id, community, degree, old
   umap_x/y for Procrustes warm-start).
2. Fetch CO_MENTIONED edges.
3. Run 2D ForceAtlas2-LinLog simulation with sqrt-clamped degree mass.
4. noverlap pass — grid-hashed iterative overlap removal.
5. Procrustes alignment to previous positions (mental-map stability).
6. Rescale core to radius _FALLBACK_SCALE; isolated shell at 1.45×.
7. z = community-relief depth (deterministic sha1 hash per community).
8. Write x/y/z to Neo4j.
9. Compute and store community map artifacts in Redis.
10. Bust serving cache.

Cadence
-------
Runs nightly per ``scheduler.py``.  Manual trigger available via the
processor router.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
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
_MAX_EDGES = int(os.getenv("UMAP_MAX_EDGES", "100000"))
_UMAP_N_NEIGHBORS = int(os.getenv("UMAP_N_NEIGHBORS", "15"))
_UMAP_MIN_DIST = float(os.getenv("UMAP_MIN_DIST", "0.1"))
_FALLBACK_SCALE = 10.0  # bounding-box radius for the connected core
_FORCE_ITERATIONS = int(os.getenv("UMAP_FORCE_ITERATIONS", "150"))
_FORCE_REPULSION = float(os.getenv("UMAP_FORCE_REPULSION", "0.12"))
_FORCE_GRAVITY = float(os.getenv("UMAP_FORCE_GRAVITY", "0.008"))
# Community-anchored cohesion: linear spring toward the node's Leiden-community
# centroid. This is what makes hulls render as coherent regions instead of
# interleaved spaghetti — the Phase 0 silhouette gate measured -0.85 without it.
_FORCE_COMMUNITY_PULL = float(os.getenv("UMAP_FORCE_COMMUNITY_PULL", "1.5"))
# Hulls only for communities with at least this many members — hundreds of
# tiny-community outlines are cartographic noise, not regions.
_MIN_HULL_MEMBERS = int(os.getenv("UMAP_MIN_HULL_MEMBERS", "8"))

# noverlap pass: base radius + per-degree increment (layout units)
_NOVERLAP_BASE_RADIUS = 0.04
_NOVERLAP_DEGREE_COEFF = 0.03
_NOVERLAP_ITERATIONS = 25

# Minimum anchor count to run Procrustes (fewer = numerically unstable)
_PROCRUSTES_MIN_ANCHORS = 10

# Community z-relief amplitude (layout units)
_Z_RELIEF_AMPLITUDE = 3.0

# Community artifacts
_COMMUNITY_MAP_REDIS_KEY = "cerid:graph:map:communities"
_SILHOUETTE_SAMPLE = 800  # max nodes sampled for silhouette score


class ComputeUmap3DJob(BaseJob):
    """Nightly job: 2D cartographic layout + community artifacts for Constellation.

    Idempotent: each run overwrites stored coordinates.  Entities without
    usable embeddings fall back to a deterministic community-spiral warm
    start.
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

        edges = await asyncio.to_thread(self._fetch_edges, driver)
        try:
            coords = await asyncio.to_thread(self._force_layout, entities, edges)
        except Exception as exc:  # noqa: BLE001 — layout must never kill the job
            log_swallowed_error("processor.compute_umap_3d.force_layout", exc)
            coords = self._fallback_layout(entities)
        await progress_cb(0.7)

        # Build degree map and 2D position array for artifact computation.
        import numpy as np  # noqa: PLC0415

        pos2d = np.array([[c["x"], c["y"]] for c in coords], dtype=np.float64)
        degree_map: dict[str, float] = {}
        for e_row in entities:
            degree_map[e_row["id"]] = float(e_row.get("_degree") or 0.0)

        self._store_community_artifacts(entities, pos2d, degree_map, driver=driver)

        await asyncio.to_thread(self._write_coords, driver, coords)
        self._bust_serving_cache()
        await progress_cb(1.0)
        method = coords[0]["method"] if coords else "none"
        logger.info("compute_umap_3d.wrote count=%d method=%s", len(coords), method)
        return JobResult(
            job_id=f"compute_umap_3d:{self._tenant_id}",
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata={"count": len(coords), "method": method},
        )

    def _bust_serving_cache(self) -> None:
        """Drop the /graph/embeddings/3d Redis cache after writing coords.

        Lives in the job (not the trigger) so every execution path — nightly
        cron, manual run-now, on-ingest subscriber — leaves a fresh cache and
        the Constellation picks up the new projection on its next poll.
        """
        try:
            from app.deps import get_redis  # noqa: PLC0415

            redis = get_redis()
            if redis is None:
                return
            dropped = 0
            for key in redis.scan_iter(match="cerid:graph:emb3d:*", count=200):
                redis.delete(key)
                dropped += 1
            if dropped:
                logger.info("compute_umap_3d: busted %d serving-cache key(s)", dropped)
        except Exception as exc:  # noqa: BLE001 — cache bust is best-effort
            log_swallowed_error("processor.compute_umap_3d.cache_bust", exc)

    # -----------------------------------------------------------------
    # Neo4j I/O
    # -----------------------------------------------------------------

    def _fetch_entities(self, driver: Any) -> list[dict[str, Any]]:
        cypher = f"""
            MATCH (e:Entity)
            WHERE e.canonical_id IS NOT NULL
            OPTIONAL MATCH (e)-[:CO_MENTIONED]-()
            WITH e, count(*) AS deg
            RETURN
                e.canonical_id AS id,
                coalesce(e.name, e.canonical_id) AS name,
                coalesce(e.entity_type, e.type) AS type,
                e.community_id AS community,
                coalesce(e.mention_count, 0) AS mention_count,
                coalesce(e.trust_state, 'unknown') AS trust_state,
                e.umap_x AS old_x,
                e.umap_y AS old_y,
                deg AS _degree
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
    # Layout helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _hash01(s: str) -> float:
        """Deterministic float in [0, 1) from a string via SHA1."""
        h = hashlib.sha1(  # noqa: S324
            s.encode("utf-8"),
            usedforsecurity=False,
        ).digest()
        return int.from_bytes(h[:4], "big") / 0x1_0000_0000

    def _fallback_layout(self, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Community-cluster 2D layout. Stable per-entity coords.

        Each community gets a centroid on a circle of radius _FALLBACK_SCALE
        via golden-ratio distribution.  Within a community entities sit at
        the centroid plus a per-entity hash offset.  z is community relief.
        """
        if not entities:
            return []

        by_community: dict[str | None, list[dict[str, Any]]] = {}
        for ent in entities:
            by_community.setdefault(ent.get("community"), []).append(ent)

        community_keys = sorted(by_community.keys(), key=lambda k: (k is None, k or ""))
        golden = math.pi * (3 - math.sqrt(5))
        centroids_2d: dict[str | None, tuple[float, float]] = {}
        for i, key in enumerate(community_keys):
            theta = golden * i
            cx = math.cos(theta) * _FALLBACK_SCALE
            cy = math.sin(theta) * _FALLBACK_SCALE
            centroids_2d[key] = (cx, cy)

        out: list[dict[str, Any]] = []
        for key, members in by_community.items():
            cx, cy = centroids_2d[key]
            spread = _FALLBACK_SCALE / (2.0 * math.sqrt(max(1, len(members))))
            for ent in members:
                h = hashlib.sha1(  # noqa: S324
                    ent["id"].encode("utf-8"),
                    usedforsecurity=False,
                ).digest()
                u1 = int.from_bytes(h[0:2], "big") / 65535
                u2 = int.from_bytes(h[2:4], "big") / 65535
                z1 = math.sqrt(-2 * math.log(max(1e-9, u1))) * math.cos(2 * math.pi * u2)
                z2 = math.sqrt(-2 * math.log(max(1e-9, u1))) * math.sin(2 * math.pi * u2)
                comm_key = str(ent.get("community") or ent["id"])
                z_val = (self._hash01(comm_key) - 0.5) * _Z_RELIEF_AMPLITUDE
                out.append({
                    "id": ent["id"],
                    "x": cx + z1 * spread,
                    "y": cy + z2 * spread,
                    "z": z_val,
                    "method": "fallback",
                })
        return out

    # -----------------------------------------------------------------
    # Layout — 2D ForceAtlas2 (Cartographer Phase 0)
    # -----------------------------------------------------------------

    def _fetch_edges(self, driver: Any) -> list[tuple[str, str, float]]:
        """CO_MENTIONED pairs + weight — the springs of the force layout."""
        cypher = f"""
            MATCH (a:Entity)-[r:CO_MENTIONED]->(b:Entity)
            WHERE a.canonical_id IS NOT NULL AND b.canonical_id IS NOT NULL
            RETURN a.canonical_id AS s, b.canonical_id AS t,
                   coalesce(r.weight, 1.0) AS w
            LIMIT {_MAX_EDGES}
        """
        try:
            with driver.session() as session:
                return [
                    (row["s"], row["t"], float(row["w"]))
                    for row in session.run(cypher).data()
                ]
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed_error("compute_umap_3d._fetch_edges", exc)
            return []

    def _force_layout(
        self,
        entities: list[dict[str, Any]],
        edges: list[tuple[str, str, float]],
    ) -> list[dict[str, Any]]:
        """2D ForceAtlas2-LinLog layout with noverlap and Procrustes alignment.

        Algorithm:
          1. Warm-start from the deterministic fallback layout (2D).
          2. FA2 LinLog + degree-weighted repulsion (sqrt-clamped mass) for
             _FORCE_ITERATIONS steps — same knobs as before (_FORCE_REPULSION,
             _FORCE_GRAVITY).
          3. noverlap pass: grid-hashed iterative overlap removal (25 iters).
          4. Procrustes alignment: map new positions onto old umap_x/y coords
             so recomputes don't flip or rotate the map.
          5. Rescale connected core to _FALLBACK_SCALE; isolated shell at 1.45×.
          6. z = (hash01(community_id) - 0.5) * _Z_RELIEF_AMPLITUDE — same
             x/y for both 2D and 3D views; deterministic per community.
        """
        import numpy as np  # noqa: PLC0415 — heavy import deferred to job runtime

        n = len(entities)
        index_of = {e["id"]: i for i, e in enumerate(entities)}

        # Warm start from the deterministic 2D fallback layout.
        seed = self._fallback_layout(entities)
        pos_all = np.array([[r["x"], r["y"]] for r in seed], dtype=np.float64)

        # Edge arrays (drop self-loops + out-of-scope endpoints).
        src, dst, wgt = [], [], []
        max_w = 1.0
        for s, t, w in edges:
            si, ti = index_of.get(s), index_of.get(t)
            if si is None or ti is None or si == ti:
                continue
            src.append(si)
            dst.append(ti)
            wgt.append(w)
            max_w = max(max_w, w)
        if not src:
            # No usable edges — enrich fallback with z coords and return.
            return self._enrich_z(seed, entities)

        src_all = np.array(src, dtype=np.int64)
        dst_all = np.array(dst, dtype=np.int64)
        w_all = np.log1p(np.array(wgt)) / math.log1p(max_w)

        # Degree array: actual graph degree from edges.
        degree = np.zeros(n)
        np.add.at(degree, src_all, 1)
        np.add.at(degree, dst_all, 1)

        connected = np.where(degree > 0)[0]
        isolated = np.where(degree == 0)[0]
        remap = -np.ones(n, dtype=np.int64)
        remap[connected] = np.arange(connected.size)

        pos = pos_all[connected].copy()  # shape (m, 2)
        # sqrt-clamped mass so the 293-degree hub anchors instead of exploding.
        mass = 1.0 + np.sqrt(degree[connected])
        src_a = remap[src_all]
        dst_a = remap[dst_all]
        w_a = w_all

        m = connected.size
        extent = float(np.sqrt((pos * pos).sum(axis=1)).max()) or _FALLBACK_SCALE
        kr = _FORCE_REPULSION
        kg = _FORCE_GRAVITY
        kp = _FORCE_COMMUNITY_PULL
        logger.info(
            "compute_umap_3d.force params kr=%s kg=%s kp=%s iters=%s m=%d",
            kr, kg, kp, _FORCE_ITERATIONS, m,
        )
        temperature = extent / 5.0
        cooling = (0.015 / temperature) ** (1.0 / _FORCE_ITERATIONS)
        chunk = 512

        # Community index per connected node (None-community nodes become
        # singletons — their centroid is themselves, so the pull is a no-op).
        comm_of: dict[str, int] = {}
        comm_idx = np.zeros(m, dtype=np.int64)
        for local_i, global_i in enumerate(connected):
            key = str(entities[int(global_i)].get("community") or f"__solo:{global_i}")
            if key not in comm_of:
                comm_of[key] = len(comm_of)
            comm_idx[local_i] = comm_of[key]
        n_comms = len(comm_of)
        comm_counts = np.bincount(comm_idx, minlength=n_comms).astype(np.float64)

        for _ in range(_FORCE_ITERATIONS):
            disp = np.zeros_like(pos)  # (m, 2)

            # FA2 repulsion: kr·m_i·m_j / d, chunked for memory.
            for start in range(0, m, chunk):
                end = min(start + chunk, m)
                delta = pos[start:end, None, :] - pos[None, :, :]  # (c, m, 2)
                dist2 = (delta * delta).sum(axis=2)                  # (c, m)
                np.clip(dist2, 1e-4, None, out=dist2)
                f = kr * (mass[start:end, None] * mass[None, :]) / dist2  # (c, m)
                disp[start:end] += (f[:, :, None] * delta).sum(axis=1)

            # LinLog attraction: w·log(1+d) toward each other.
            delta_e = pos[src_a] - pos[dst_a]  # (E, 2)
            dist_e = np.sqrt((delta_e * delta_e).sum(axis=1)) + 1e-9
            pull = (w_a * np.log1p(dist_e) / dist_e)[:, None] * delta_e
            np.subtract.at(disp, src_a, pull)
            np.add.at(disp, dst_a, pull)

            # Weak gravity keeps components on stage.
            r = np.sqrt((pos * pos).sum(axis=1)) + 1e-9
            disp -= (kg * mass / r)[:, None] * pos

            # Community cohesion: linear spring toward the node's Leiden
            # centroid so communities are spatially coherent and the hull
            # layer renders real regions (silhouette gate driver).
            if kp > 0:
                centroids = np.zeros((n_comms, 2))
                np.add.at(centroids, comm_idx, pos)
                centroids /= comm_counts[:, None]
                disp += kp * (centroids[comm_idx] - pos)

            # Apply, capped by temperature; cool.
            length = np.sqrt((disp * disp).sum(axis=1)) + 1e-9
            step = np.minimum(length, temperature)
            pos += disp / length[:, None] * step[:, None]
            temperature *= cooling

        # noverlap pass: iterative overlap removal using a grid hash.
        pos = self._noverlap(pos, degree[connected])

        # Rescale core to _FALLBACK_SCALE.
        radius = float(np.sqrt((pos * pos).sum(axis=1)).max())
        if radius > 1e-9:
            pos *= _FALLBACK_SCALE / radius
        pos_all[connected] = pos

        # Isolated shell.
        if isolated.size:
            iso = pos_all[isolated]
            iso_r = np.sqrt((iso * iso).sum(axis=1)) + 1e-9
            pos_all[isolated] = iso / iso_r[:, None] * (_FALLBACK_SCALE * 1.45)

        # Procrustes alignment to previous positions.
        old_x = np.array([e.get("old_x") for e in entities], dtype=object)
        old_y = np.array([e.get("old_y") for e in entities], dtype=object)
        pos_all = self._procrustes_align(pos_all, old_x, old_y)

        # Build output with z community relief.
        result: list[dict[str, Any]] = []
        for i in range(n):
            comm_key = str(entities[i].get("community") or entities[i]["id"])
            z_val = (self._hash01(comm_key) - 0.5) * _Z_RELIEF_AMPLITUDE
            result.append({
                "id": entities[i]["id"],
                "x": float(pos_all[i, 0]),
                "y": float(pos_all[i, 1]),
                "z": z_val,
                "method": "force",
            })
        return result

    @staticmethod
    def _noverlap(
        pos: Any,  # np.ndarray (m, 2)
        degree: Any,  # np.ndarray (m,)
    ) -> Any:
        """Iterative noverlap removal via grid-hashed neighborhoods.

        Node radius_i = _NOVERLAP_BASE_RADIUS + _NOVERLAP_DEGREE_COEFF·sqrt(degree_i)
        in layout units.  Uses a grid hash so the inner loop is bounded to
        same-cell + adjacent-cell pairs rather than all N² pairs.
        """
        import numpy as np  # noqa: PLC0415

        m = pos.shape[0]
        radii = _NOVERLAP_BASE_RADIUS + _NOVERLAP_DEGREE_COEFF * np.sqrt(degree)
        max_r = float(radii.max())
        cell_size = max_r * 2.0

        for _ in range(_NOVERLAP_ITERATIONS):
            # Build grid hash: map each node to its cell.
            cells_x = np.floor(pos[:, 0] / cell_size).astype(np.int64)
            cells_y = np.floor(pos[:, 1] / cell_size).astype(np.int64)
            cell_map: dict[tuple[int, int], list[int]] = {}
            for idx in range(m):
                key = (int(cells_x[idx]), int(cells_y[idx]))
                cell_map.setdefault(key, []).append(idx)

            moved = False
            for (cx, cy), members in cell_map.items():
                # Gather candidates from this cell + 8 neighbors.
                candidates: list[int] = []
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        candidates.extend(cell_map.get((cx + dx, cy + dy), []))

                # Check all pairs within the candidate set.
                for ii in range(len(members)):
                    i = members[ii]
                    for j in candidates:
                        if j <= i:
                            continue
                        dx_ij = pos[j, 0] - pos[i, 0]
                        dy_ij = pos[j, 1] - pos[i, 1]
                        dist = math.sqrt(dx_ij * dx_ij + dy_ij * dy_ij)
                        min_dist = radii[i] + radii[j]
                        if dist < min_dist and dist > 1e-9:
                            overlap = (min_dist - dist) * 0.5
                            ux = dx_ij / dist
                            uy = dy_ij / dist
                            pos[i, 0] -= ux * overlap
                            pos[i, 1] -= uy * overlap
                            pos[j, 0] += ux * overlap
                            pos[j, 1] += uy * overlap
                            moved = True
                        elif dist < 1e-9:
                            # Coincident nodes — push apart deterministically.
                            offset = min_dist * 0.5 + 1e-6
                            pos[i, 0] -= offset
                            pos[j, 0] += offset
                            moved = True
            if not moved:
                break

        return pos

    @staticmethod
    def _procrustes_align(
        new_pos: Any,      # np.ndarray (n, 2)
        old_x: Any,        # object array of float|None, length n
        old_y: Any,        # object array of float|None, length n
    ) -> Any:
        """Similarity transform mapping new positions onto old anchor positions.

        Computes centroid, scale, and rotation (including reflection) via
        SVD so recomputes don't flip or rotate the mental map.  Pure numpy.
        Skips if fewer than _PROCRUSTES_MIN_ANCHORS valid anchors.
        """
        import numpy as np  # noqa: PLC0415

        # Find rows where both old_x and old_y are valid floats.
        valid_mask = np.array([
            old_x[i] is not None and old_y[i] is not None
            for i in range(len(old_x))
        ], dtype=bool)
        n_valid = int(valid_mask.sum())
        if n_valid < _PROCRUSTES_MIN_ANCHORS:
            return new_pos

        anchor_new = new_pos[valid_mask]
        anchor_old = np.column_stack([
            [float(old_x[i]) for i in range(len(old_x)) if valid_mask[i]],
            [float(old_y[i]) for i in range(len(old_y)) if valid_mask[i]],
        ])

        # Centre both clouds.
        c_new = anchor_new.mean(axis=0)
        c_old = anchor_old.mean(axis=0)
        A = anchor_new - c_new
        B = anchor_old - c_old

        # Scale normalisation.
        scale_A = float(np.sqrt((A * A).sum()))
        scale_B = float(np.sqrt((B * B).sum()))
        if scale_A < 1e-12 or scale_B < 1e-12:
            return new_pos
        A_n = A / scale_A
        B_n = B / scale_B

        # Optimal rotation (+ reflection) via SVD of B^T A.
        M = B_n.T @ A_n
        U, _S, Vt = np.linalg.svd(M)
        R = U @ Vt  # (2, 2) rotation/reflection matrix

        # Scale factor mapping new → old scale.
        s = scale_B / scale_A

        # Apply: T(p) = s · R · (p - c_new) + c_old
        centred = new_pos - c_new
        aligned = s * (centred @ R.T) + c_old
        return aligned

    def _enrich_z(
        self,
        coords: list[dict[str, Any]],
        entities: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Add z community relief to fallback coords (no-edge path)."""
        ent_by_id = {e["id"]: e for e in entities}
        for c in coords:
            ent = ent_by_id.get(c["id"], {})
            comm_key = str(ent.get("community") or c["id"])
            c["z"] = (self._hash01(comm_key) - 0.5) * _Z_RELIEF_AMPLITUDE
        return coords

    # -----------------------------------------------------------------
    # Community map artifacts
    # -----------------------------------------------------------------

    @staticmethod
    def _first_clause(text: str, max_chars: int = 32) -> str:
        """Extract the first sentence clause from a summary for short lane labels.

        Splits on the first '. ', ': ', ', ', or newline — whichever comes
        first — and truncates to max_chars.  Returns the stripped result or
        an empty string if the input is empty.
        """
        if not text:
            return ""
        import re as _re  # noqa: PLC0415
        # LLM community summaries open with boilerplate ("The theme revolves
        # around X", "This community centers on Y") — strip the lead-in so the
        # label carries the subject, not the filler.
        stripped = _re.sub(
            r"^(?:the|this|a)?\s*"
            r"(?:theme|community|cluster|topic|content|summary)?\s*"
            r"(?:revolves?\s+around|centers?\s+(?:on|around)|focuses?\s+on|"
            r"is\s+(?:about|centered\s+(?:on|around))|covers|concerns|relates\s+to)\s+",
            "",
            text.strip(),
            flags=_re.IGNORECASE,
        )
        # Split on first sentence-ending delimiter
        parts = _re.split(r"[.:\n,]", stripped, maxsplit=1)
        first = parts[0].strip()
        if not first:
            return ""
        first = first[0].upper() + first[1:]
        return first[:max_chars]

    def _community_artifacts(
        self,
        entities: list[dict[str, Any]],
        pos2d: Any,  # np.ndarray (n, 2)
        degree_map: dict[str, float],
        *,
        driver: Any = None,
    ) -> dict[str, Any]:
        """Compute community-level map artifacts.

        Returns a dict matching the cerid:graph:map:communities schema.

        Tephra Cycle-2: queries ``Community.summary`` from Neo4j and derives
        a ``short_label`` (≤32 chars, first-clause of summary) for Timeline
        gutter, Atlas hull labels, and /graph/map.  Falls back to
        ``top_hubs[0].name`` when the summary is absent.

        Id-join normalization: ``Entity.community_id`` in Neo4j carries the
        scalar GDS int assigned by the Leiden algorithm.  ``Community.id``
        is stored as ``"{level}:{native_id}"`` (e.g. ``"0:1592"``).  We
        index by both forms so lookups succeed regardless of which form
        the entities list carries.
        """
        # Tephra Cycle-2: fetch Community.summary from Neo4j indexed by both
        # "{level}:{native_id}" and the scalar native_id.
        summary_map: dict[str, str] = {}  # both key forms → summary text
        if driver is not None:
            try:
                with driver.session() as _s:
                    result = _s.run(
                        "MATCH (c:Community) WHERE c.summary IS NOT NULL "
                        "RETURN c.id AS cid, c.summary AS summary"
                    )
                    for row in result:
                        cid_val = str(row["cid"] or "")
                        summary_text = str(row["summary"] or "")
                        if cid_val and summary_text:
                            summary_map[cid_val] = summary_text
                            # Also index by the native_id scalar if the format is "level:id"
                            parts = cid_val.split(":", 1)
                            if len(parts) == 2 and parts[1].isdigit():
                                summary_map[parts[1]] = summary_text
            except Exception as exc:  # noqa: BLE001 — summary lookup is best-effort
                log_swallowed_error(
                    "processor.compute_umap_3d.community_summary_fetch", exc
                )

        # Group entities by community.
        by_community: dict[str, list[int]] = {}
        for i, ent in enumerate(entities):
            comm = ent.get("community")
            if comm is None:
                continue
            by_community.setdefault(str(comm), []).append(i)

        community_data: list[dict[str, Any]] = []
        for comm_id, idxs in by_community.items():
            if len(idxs) < _MIN_HULL_MEMBERS:
                continue
            pts = pos2d[idxs]

            # Convex hull (Andrew's monotone chain) + Chaikin smoothing.
            hull_pts = _convex_hull(pts)
            smoothed = _chaikin(hull_pts, rounds=2)
            hull_list = [[round(float(p[0]), 3), round(float(p[1]), 3)] for p in smoothed]

            # Anchor: density argmax via 24×24 histogram over community bbox.
            anchor = _density_argmax(pts, bins=24)

            # Top-degree members.
            members_ent = [entities[i] for i in idxs]
            members_deg = [degree_map.get(entities[i]["id"], 0.0) for i in idxs]
            sorted_pairs = sorted(
                zip(members_ent, members_deg), key=lambda p: p[1], reverse=True
            )
            top_hubs = [
                {"id": e["id"], "name": e.get("name") or e["id"], "degree": int(d)}
                for e, d in sorted_pairs[:5]
            ]
            hub_name = top_hubs[0]["name"] if top_hubs else comm_id

            # Tephra Cycle-2: derive short_label from Community.summary.
            # Lookup tries both the raw comm_id and the scalar native_id.
            summary_text = summary_map.get(comm_id, "")
            if not summary_text:
                # Try "{level}:{native_id}" form (comm_id may be a bare scalar)
                parts = comm_id.split(":", 1)
                if len(parts) == 2:
                    summary_text = summary_map.get(parts[1], "")
            short_label = self._first_clause(summary_text, max_chars=32) or hub_name
            label = short_label  # backwards-compat field name

            # Trust mix.
            trust_mix: dict[str, int] = {
                "verified": 0, "partial": 0, "unverified": 0, "unknown": 0,
            }
            for ent in members_ent:
                ts = ent.get("trust_state") or "unknown"
                if ts in trust_mix:
                    trust_mix[ts] += 1
                else:
                    trust_mix["unknown"] += 1

            community_data.append({
                "id": comm_id,
                "count": len(idxs),
                "hull": hull_list,
                "anchor": [round(float(anchor[0]), 3), round(float(anchor[1]), 3)],
                "label": label,
                "short_label": short_label,
                "top_hubs": top_hubs,
                "trust_mix": trust_mix,
            })

        # Silhouette score over a sample.
        silhouette = _centroid_silhouette(entities, pos2d, by_community, _SILHOUETTE_SAMPLE)
        logger.info(
            "compute_umap_3d.silhouette score=%.4f communities=%d",
            silhouette,
            len(community_data),
        )

        return {
            "communities": community_data,
            "silhouette": round(silhouette, 4),
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

    def _store_community_artifacts(
        self,
        entities: list[dict[str, Any]],
        pos2d: Any,  # np.ndarray (n, 2)
        degree_map: dict[str, float],
        *,
        driver: Any = None,
    ) -> None:
        """Compute community artifacts and store in Redis."""
        try:
            from app.deps import get_redis  # noqa: PLC0415

            redis = get_redis()
            if redis is None:
                return
            artifacts = self._community_artifacts(entities, pos2d, degree_map, driver=driver)
            redis.set(_COMMUNITY_MAP_REDIS_KEY, json.dumps(artifacts))
        except Exception as exc:  # noqa: BLE001 — artifact storage is best-effort
            log_swallowed_error("processor.compute_umap_3d.community_artifacts", exc)


# -----------------------------------------------------------------
# Pure-numpy geometry helpers (module-level for testability)
# -----------------------------------------------------------------

def _convex_hull(pts: Any) -> Any:
    """Andrew's monotone chain convex hull.  Returns hull vertices in CCW order.

    Args:
        pts: np.ndarray (n, 2)

    Returns:
        np.ndarray (k, 2) of hull vertices.
    """
    import numpy as np  # noqa: PLC0415

    points = pts.tolist()
    points = sorted({(p[0], p[1]) for p in points})
    if len(points) < 3:
        return np.array(points)

    def cross(o: tuple, a: tuple, b: tuple) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper: list[tuple[float, float]] = []
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    hull = lower[:-1] + upper[:-1]
    return np.array(hull)


def _chaikin(pts: Any, rounds: int = 2) -> Any:
    """Chaikin corner-cutting smoothing.  Polygon stays within original bbox.

    Args:
        pts: np.ndarray (n, 2) of polygon vertices.
        rounds: number of subdivision rounds.

    Returns:
        np.ndarray of smoothed vertices.
    """
    import numpy as np  # noqa: PLC0415

    if len(pts) < 3:
        return pts
    for _ in range(rounds):
        out: list[list[float]] = []
        n = len(pts)
        for i in range(n):
            p0 = pts[i]
            p1 = pts[(i + 1) % n]
            out.append([0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1]])
            out.append([0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1]])
        pts = np.array(out)
    return pts


def _density_argmax(pts: Any, bins: int = 24) -> Any:
    """Find the densest 2D histogram cell center for a point cloud.

    Args:
        pts: np.ndarray (n, 2)
        bins: histogram resolution along each axis

    Returns:
        np.ndarray (2,) — [x, y] of the densest cell center.
    """
    import numpy as np  # noqa: PLC0415

    if len(pts) == 0:
        return np.zeros(2)
    xmin, ymin = pts.min(axis=0)
    xmax, ymax = pts.max(axis=0)
    if xmax == xmin:
        xmax = xmin + 1e-9
    if ymax == ymin:
        ymax = ymin + 1e-9

    hist, xedges, yedges = np.histogram2d(
        pts[:, 0], pts[:, 1],
        bins=bins,
        range=[[xmin, xmax], [ymin, ymax]],
    )
    flat_idx = int(np.argmax(hist))
    ix, iy = divmod(flat_idx, bins)
    cx = (xedges[ix] + xedges[ix + 1]) * 0.5
    cy = (yedges[iy] + yedges[iy + 1]) * 0.5
    return np.array([cx, cy])


def _centroid_silhouette(
    entities: list[dict[str, Any]],
    pos2d: Any,  # np.ndarray (n, 2)
    by_community: dict[str, list[int]],
    max_sample: int = 800,
) -> float:
    """Simplified centroid silhouette: (b - a) / max(a, b).

    a = distance from node to its community centroid.
    b = min distance from node to any other community centroid.

    Returns the mean score over a random sample of nodes (deterministic
    by position — no random seed needed).
    """
    import numpy as np  # noqa: PLC0415

    comms = [c for c, idxs in by_community.items() if len(idxs) >= 3]
    if len(comms) < 2:
        return 0.0

    centroids: dict[str, Any] = {
        c: pos2d[by_community[c]].mean(axis=0) for c in comms
    }

    # Collect (idx, community_id) pairs for sampling.
    all_pairs: list[tuple[int, str]] = []
    for c in comms:
        for idx in by_community[c]:
            all_pairs.append((idx, c))

    # Deterministic subsample: take every k-th entry.
    if len(all_pairs) > max_sample:
        step = len(all_pairs) // max_sample
        all_pairs = all_pairs[::step][:max_sample]

    scores: list[float] = []
    for idx, own_comm in all_pairs:
        p = pos2d[idx]
        a = float(np.sqrt(((p - centroids[own_comm]) ** 2).sum()))
        other_dists = [
            float(np.sqrt(((p - centroids[c]) ** 2).sum()))
            for c in comms if c != own_comm
        ]
        if not other_dists:
            continue
        b = min(other_dists)
        denom = max(a, b)
        if denom < 1e-9:
            scores.append(0.0)
        else:
            scores.append((b - a) / denom)

    if not scores:
        return 0.0
    return float(sum(scores) / len(scores))
