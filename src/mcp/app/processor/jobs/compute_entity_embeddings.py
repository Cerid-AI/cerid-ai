# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compute and store a per-entity embedding vector via mean-pooled chunk embeddings.

Strategy
--------
Each ``(:Entity)`` node gains an ``embedding`` property (JSON-encoded
list[float]) derived from the Chroma chunk embeddings referenced by its
``MENTIONS`` edges.  The pipeline is:

1. Fetch all ``(:Entity)`` nodes with their MENTIONS ``chunk_ids`` (collected
   as a distinct set across all inbound MENTIONS edges).
2. For each entity:
   a. Fetch those chunk vectors from Chroma via ``collection.get``
      with ``include=["embeddings"]`` across all domain collections.
   b. Mean-pool the retrieved vectors (numpy) and L2-normalise → entity embedding.
   c. Fallback: if no chunk vectors are retrievable, embed the entity's
      canonical name via the ONNX embedding function.
   d. Skip: if even the name-embed path is unavailable, skip without writing.
3. Batch-write ``embedding`` (JSON list[float]), ``embedding_model`` (settings
   name), and ``embedding_computed_at`` (ISO) back to Neo4j via UNWIND.

Runs nightly BEFORE ``compute_umap_3d`` so semantic kNN edges and layout both
pick up fresh embeddings.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import numpy as np

import config
from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.compute_entity_embeddings")

# Max chunk IDs fetched from a single collection.get call — avoids OOM on large
# entity graphs.  Entities with more chunk refs get batched internally.
_CHUNK_BATCH_SIZE = 200


class ComputeEntityEmbeddingsJob(BaseJob):
    """Nightly job: compute and store one embedding vector per Entity node.

    Idempotent: each run overwrites existing ``embedding`` / ``embedding_model``
    / ``embedding_computed_at`` properties.
    """

    job_type = "compute_entity_embeddings"

    def __init__(self, tenant_id: str = "default") -> None:
        self._tenant_id = tenant_id

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        return CostEstimate(
            estimated_tokens_in=0,
            estimated_tokens_out=0,
            model="cpu/embeddings",
            estimated_usd=Decimal("0.00"),
            confidence="high",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        await progress_cb(0.0)
        from app.deps import get_neo4j  # noqa: PLC0415

        driver = get_neo4j()
        if driver is None:
            logger.warning("compute_entity_embeddings: neo4j unavailable, skipping")
            return JobResult(
                job_id=f"compute_entity_embeddings:{self._tenant_id}",
                actual_tokens_in=0,
                actual_tokens_out=0,
                metadata={"status": "skipped", "reason": "neo4j unavailable"},
            )

        # Fetch entity → chunk_ids mapping from Neo4j.
        entity_rows = await asyncio.to_thread(self._fetch_entities_with_chunks, driver)
        if not entity_rows:
            await progress_cb(1.0)
            return JobResult(
                job_id=f"compute_entity_embeddings:{self._tenant_id}",
                actual_tokens_in=0,
                actual_tokens_out=0,
                metadata={"status": "no_op", "count": 0},
            )

        await progress_cb(0.1)
        logger.info(
            "compute_entity_embeddings.fetched entities=%d", len(entity_rows)
        )

        # Gather the Chroma client and domain collections.
        chroma_client, collections = await asyncio.to_thread(self._open_collections)
        await progress_cb(0.2)

        # Build a chunk_id → embedding index across all collections in one pass.
        all_chunk_ids: set[str] = set()
        for row in entity_rows:
            all_chunk_ids.update(row["chunk_ids"])

        chunk_embedding_index: dict[str, np.ndarray] = {}
        if all_chunk_ids:
            chunk_embedding_index = await asyncio.to_thread(
                self._fetch_chunk_embeddings, collections, list(all_chunk_ids)
            )
        await progress_cb(0.5)
        logger.info(
            "compute_entity_embeddings.chunk_vectors fetched=%d of %d chunk_ids",
            len(chunk_embedding_index),
            len(all_chunk_ids),
        )

        # Resolve the embedding function for the name-embed fallback.
        embed_fn = await asyncio.to_thread(self._get_embed_fn)

        # Compute one embedding per entity.
        model_name: str = config.EMBEDDING_MODEL
        rows_to_write: list[dict[str, Any]] = []
        skipped = 0
        for row in entity_rows:
            vec = self._compute_embedding(
                row, chunk_embedding_index, embed_fn
            )
            if vec is None:
                skipped += 1
                continue
            rows_to_write.append({
                "id": row["id"],
                "embedding": json.dumps(vec.tolist()),
                "embedding_model": model_name,
            })

        await progress_cb(0.85)

        # Batch-write to Neo4j.
        if rows_to_write:
            await asyncio.to_thread(self._write_embeddings, driver, rows_to_write)

        await progress_cb(1.0)
        logger.info(
            "compute_entity_embeddings.done written=%d skipped=%d",
            len(rows_to_write),
            skipped,
        )
        return JobResult(
            job_id=f"compute_entity_embeddings:{self._tenant_id}",
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata={
                "written": len(rows_to_write),
                "skipped": skipped,
                "model": model_name,
            },
        )

    # -----------------------------------------------------------------
    # Neo4j I/O
    # -----------------------------------------------------------------

    def _fetch_entities_with_chunks(self, driver: Any) -> list[dict[str, Any]]:
        """Fetch all Entity nodes with their distinct chunk_ids from MENTIONS edges.

        Returns a list of dicts: {id, name, chunk_ids: list[str]}.
        chunk_ids is the union of all MENTIONS.chunk_ids across all artifacts.
        """
        cypher = """
            MATCH (e:Entity)
            WHERE e.canonical_id IS NOT NULL
            OPTIONAL MATCH ()-[m:MENTIONS]->(e)
            WHERE m.chunk_ids IS NOT NULL
            RETURN
                e.canonical_id AS id,
                coalesce(e.name, e.canonical_id) AS name,
                collect(DISTINCT m.chunk_ids) AS chunk_ids_json_list
        """
        try:
            with driver.session() as session:
                rows = session.run(cypher).data()
            result: list[dict[str, Any]] = []
            for row in rows:
                chunk_ids: list[str] = []
                for json_str in (row.get("chunk_ids_json_list") or []):
                    if json_str:
                        try:
                            parsed = json.loads(json_str)
                            if isinstance(parsed, list):
                                chunk_ids.extend(str(c) for c in parsed if c)
                        except (json.JSONDecodeError, TypeError):
                            pass  # silent-catch-allowed: bad JSON in chunk_ids is best-effort
                result.append({
                    "id": row["id"],
                    "name": row.get("name") or row["id"],
                    "chunk_ids": list(dict.fromkeys(chunk_ids)),  # deduplicate, preserve order
                })
            return result
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed_error("compute_entity_embeddings._fetch_entities_with_chunks", exc)
            return []

    def _write_embeddings(self, driver: Any, rows: list[dict[str, Any]]) -> None:
        """Batch-write entity embeddings via UNWIND (mirrors _write_coords pattern)."""
        if not rows:
            return
        now = datetime.now(timezone.utc).isoformat()
        cypher = """
            UNWIND $rows AS row
            MATCH (e:Entity {canonical_id: row.id})
            SET
                e.embedding = row.embedding,
                e.embedding_model = row.embedding_model,
                e.embedding_computed_at = $now
        """
        try:
            with driver.session() as session:
                session.run(cypher, rows=rows, now=now)
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed_error("compute_entity_embeddings._write_embeddings", exc)

    # -----------------------------------------------------------------
    # Chroma helpers
    # -----------------------------------------------------------------

    def _open_collections(self) -> tuple[Any, list[Any]]:
        """Return (chroma_client, [collection, ...]) for all domain collections.

        Returns an empty list of collections when Chroma is unavailable — the
        caller falls through to name-embed for every entity.
        """
        try:
            from app.deps import get_chroma  # noqa: PLC0415

            chroma_client = get_chroma()
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("compute_entity_embeddings._open_collections.get_chroma", exc)
            return None, []

        if chroma_client is None:
            return None, []

        try:
            raw_colls = chroma_client.list_collections()
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("compute_entity_embeddings._open_collections.list", exc)
            return chroma_client, []

        collections: list[Any] = []
        for coll_info in raw_colls:
            # list_collections() may return Collection objects or dicts
            coll_name = getattr(coll_info, "name", None) or (
                coll_info.get("name") if isinstance(coll_info, dict) else None
            )
            if not coll_name:
                continue
            try:
                coll = chroma_client.get_collection(name=coll_name)
                collections.append(coll)
            except Exception as exc:  # noqa: BLE001
                log_swallowed_error(
                    "compute_entity_embeddings._open_collections.get",
                    exc,
                    context={"collection": coll_name},
                )
        return chroma_client, collections

    def _fetch_chunk_embeddings(
        self,
        collections: list[Any],
        chunk_ids: list[str],
    ) -> dict[str, np.ndarray]:
        """Fetch embeddings for the given chunk IDs across all collections.

        Returns a dict mapping chunk_id → np.ndarray (float32 vector).
        IDs not found in any collection are absent from the result.
        """
        if not collections or not chunk_ids:
            return {}

        found: dict[str, np.ndarray] = {}
        remaining = set(chunk_ids)

        for collection in collections:
            if not remaining:
                break
            batch_ids = list(remaining)
            # Process in batches to avoid OOM on large collections.
            for start in range(0, len(batch_ids), _CHUNK_BATCH_SIZE):
                sub_ids = batch_ids[start : start + _CHUNK_BATCH_SIZE]
                try:
                    result = collection.get(ids=sub_ids, include=["embeddings"])
                except Exception as exc:  # noqa: BLE001
                    log_swallowed_error(
                        "compute_entity_embeddings._fetch_chunk_embeddings.get", exc
                    )
                    continue

                fetched_ids: list[str] = result.get("ids") or []
                _raw_emb = result.get("embeddings")
                embeddings: list[Any] = list(_raw_emb) if _raw_emb is not None else []
                for i, cid in enumerate(fetched_ids):
                    if i < len(embeddings) and embeddings[i] is not None:
                        found[cid] = np.asarray(embeddings[i], dtype=np.float32)
                        remaining.discard(cid)

        return found

    # -----------------------------------------------------------------
    # Embedding helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _get_embed_fn() -> Any:
        """Return the ONNX embedding callable, or None if unavailable."""
        try:
            from core.utils.embeddings import get_embedding_function  # noqa: PLC0415

            return get_embedding_function()
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("compute_entity_embeddings._get_embed_fn", exc)
            return None

    def _compute_embedding(
        self,
        entity: dict[str, Any],
        chunk_index: dict[str, np.ndarray],
        embed_fn: Any,
    ) -> np.ndarray | None:
        """Produce one L2-normalised embedding for an entity.

        Priority:
        1. Mean-pool the chunk vectors from chunk_index.
        2. Fallback: embed the canonical name via embed_fn.
        3. Skip: return None (caller omits from write batch).
        """
        # 1. Mean-pool chunk vectors.
        vecs: list[np.ndarray] = []
        for cid in entity.get("chunk_ids") or []:
            vec = chunk_index.get(cid)
            if vec is not None:
                vecs.append(vec)

        if vecs:
            pooled = np.mean(np.stack(vecs, axis=0), axis=0)
            return self._l2_normalize(pooled)

        # 2. Name-embed fallback.
        if embed_fn is not None:
            name = entity.get("name") or entity.get("id") or ""
            if name:
                try:
                    result = embed_fn([name])
                    if result:
                        return self._l2_normalize(np.asarray(result[0], dtype=np.float32))
                except Exception as exc:  # noqa: BLE001
                    log_swallowed_error(
                        "compute_entity_embeddings._compute_embedding.name_embed",
                        exc,
                        context={"entity_id": entity.get("id")},
                    )

        # 3. Skip.
        return None

    @staticmethod
    def _l2_normalize(vec: np.ndarray) -> np.ndarray:
        norm = float(np.linalg.norm(vec))
        if norm < 1e-12:
            return vec.astype(np.float32)
        return (vec / norm).astype(np.float32)
