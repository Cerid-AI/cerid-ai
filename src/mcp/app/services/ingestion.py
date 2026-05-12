# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Core ingestion service functions.

Extracted from routers/ingestion.py to eliminate the circular import
between agents/memory.py → routers/ingestion.py. Routers and agents
both import from this service layer.

Two-phase write (Phase O.1)
---------------------------
Every new ingest now follows a two-phase commit protocol:

1. **Stage** — write Chroma chunks with ``cerid_state="pending"`` and
   ``cerid_pending_at=<iso>``.  An ``idempotency_key`` (SHA-256 of
   ``content + source_uri + tenant``) is stored so a re-run of the same
   input finds the existing pending rows and skips the duplicate write.

2. **Commit Neo4j** — call ``graph.create_artifact``.  On success, flip
   all staged chunks to ``cerid_state="committed"`` via
   ``_flip_chunks_committed``.  On failure, leave the Chroma rows in
   ``pending`` state — the ``IngestRecoveryJob`` in
   ``app/services/ingest_recovery.py`` scans for rows older than 60 s and
   either rolls them forward or purges them.

Retrieval gate (Phase O.1)
--------------------------
The main Chroma query in ``core/agents/query_agent.py`` passes the caller's
``metadata_filter`` through ``with_tenant_scope``.  That helper already
accepts an arbitrary dict that ChromaDB ANDs with the tenant scope.
Callers that want to exclude pending rows should add
``{"cerid_state": {"$ne": "pending"}}`` to their ``metadata_filter``.
The retrieval chokepoint list and the filter application are documented in
``_PENDING_FILTER`` below; query_agent applies it automatically when
``CERID_FILTER_PENDING_CHUNKS`` (default True) is set.  This module
exposes ``PENDING_STATE_FILTER`` for import by query_agent.

Public API is unchanged — all callers of ``ingest_content`` and
``ingest_file`` continue to work without modification.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any

import config
from app.db import neo4j as graph
from app.deps import get_chroma, get_neo4j, get_redis
from app.parsers import parse_file
from core.context.identity import get_tenant_id
from core.utils import cache
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso
from utils.chunker import (
    chunk_text,
    chunk_with_parents,
    make_context_header,
    parent_child_enabled,
)
from utils.metadata import ai_categorize, extract_metadata, extract_metadata_minimal

logger = logging.getLogger("ai-companion")

# ---------------------------------------------------------------------------
# Phase O.1 — two-phase write constants
# ---------------------------------------------------------------------------

#: ChromaDB ``where`` sub-clause that excludes pending (un-committed) chunks.
#: Imported by ``core/agents/query_agent.py`` to apply at every Chroma
#: query site.  Using ``$ne`` because Chroma doesn't support ``$not``
#: at the top level.
PENDING_STATE_FILTER: dict[str, Any] = {"cerid_state": {"$ne": "pending"}}


#: Frontmatter keys that travel through other code paths (taxonomy +
#: alias resolution + timestamp override) and so MUST NOT be re-written
#: as raw Artifact properties.  ``tags`` goes through ``tags_json`` →
#: ``create_artifact`` → ``:TAGGED_WITH``; ``aliases`` goes into
#: ``resolve_pending_artifacts``; ``created`` / ``updated`` are coerced
#: into ``created_at`` / ``updated_at`` Artifact columns explicitly.
_FRONTMATTER_KEYS_HANDLED_ELSEWHERE: frozenset[str] = frozenset(
    {"tags", "aliases", "created", "updated"},
)


def _frontmatter_to_artifact_props(
    frontmatter: dict[str, Any],
    *,
    created_override: str | None = None,
    updated_override: str | None = None,
) -> dict[str, Any]:
    """Project the frontmatter dict onto Artifact node property names.

    RAG Cycle C2.2 — collapses the user-facing key shape into the
    Neo4j-safe shape:

    * Reserved scalars (``status``, ``cssclass``, ``source``) → same
      property name on the Artifact node.
    * ``cerid:<name>`` custom keys → ``cerid_<name>`` (Neo4j property
      names can't contain colons).
    * ``created`` / ``updated`` → ``created_at`` / ``updated_at`` using
      the caller-supplied ISO-coerced overrides (the frontmatter values
      may be ``datetime`` objects which Neo4j can store but we keep
      timestamps as strings for cross-store consistency).
    * ``tags`` / ``aliases`` are skipped — they flow through other
      paths (taxonomy + alias resolution).
    * Non-string non-primitive values (nested dicts) are skipped to
      stay within Neo4j's property-type matrix.
    """
    out: dict[str, Any] = {}
    for key, value in frontmatter.items():
        if not isinstance(key, str):
            continue
        if key in _FRONTMATTER_KEYS_HANDLED_ELSEWHERE:
            continue
        if value is None:
            continue
        if key.startswith("cerid:"):
            # ``cerid:foo`` → ``cerid_foo``.  Sanitize the suffix to a
            # legal Neo4j property identifier — any non-alphanumeric
            # char (colons, spaces, dashes, dots) collapses to an
            # underscore. Prevents the inline ``a.cerid_foo bar`` Cypher
            # generated by set_artifact_properties from being invalid.
            import re as _re
            suffix = _re.sub(r"[^A-Za-z0-9_]+", "_", key[len("cerid:"):]).strip("_")
            if not suffix:
                continue
            prop_name = "cerid_" + suffix
            out[prop_name] = _coerce_artifact_property_value(value)
            continue
        # Reserved scalars land under their plain name.
        out[key] = _coerce_artifact_property_value(value)

    if created_override:
        out["created_at"] = created_override
    if updated_override:
        out["updated_at"] = updated_override

    return out


def _coerce_artifact_property_value(value: Any) -> Any:
    """Coerce a frontmatter value to a Neo4j-safe property type.

    Neo4j supports str / int / float / bool / None plus homogeneous
    lists of those.  Dicts are stringified (frontmatter rarely has
    nested mappings, but we don't want to drop them silently).  Lists
    of mixed types are str-cast for safety.
    """
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        if all(isinstance(v, str) for v in value):
            return list(value)
        return [str(v) for v in value]
    # datetime / date / dict / other — JSON-encode so the property
    # round-trips through retrieval.
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _coerce_frontmatter_timestamp(value: Any) -> str:
    """Coerce a frontmatter ``created`` / ``updated`` value to ISO string.

    PyYAML's ``safe_load`` auto-parses ``YYYY-MM-DD`` and full ISO
    timestamps into ``datetime.date`` / ``datetime.datetime`` objects.
    Neo4j stores these as strings on the Artifact node so retrieval
    serialisation (JSON, openapi) doesn't need date-aware encoders.
    Strings pass through unchanged; anything else falls back to ``str()``.
    """
    import datetime as _dt
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return str(value)


def _coerce_chroma_meta(value: Any) -> Any:
    """Coerce a metadata value into ChromaDB-compatible primitives.

    ChromaDB rejects ``list``/``dict``/``set``/``tuple`` values (its
    metadata schema is ``str | int | float | bool | None``). The Phase 2b
    parsers emit Python-native ``column_headers: list[str]``,
    ``heading_path: list[str]``, ``cells: list[str]``, etc. — JSON-encode
    those at the write boundary so retrieval code can decode back when
    needed (and so the legacy chunk_text path with primitive-only
    metadata is unaffected).
    """
    if isinstance(value, (list, dict, set, tuple)):
        return json.dumps(value if not isinstance(value, set) else sorted(value))
    return value


def validate_file_path(file_path: str) -> Path:
    """Ensure file_path resolves within the configured archive directory."""
    allowed_root = Path(config.ARCHIVE_PATH).resolve()
    resolved = Path(file_path).resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError:
        raise ValueError(
            f"Path '{file_path}' is outside the allowed archive directory ({allowed_root})."
        )
    return resolved


# ── Private helpers ────────────────────────────────────────────────────────────

def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _idempotency_key(content: str, source_uri: str, tenant: str) -> str:
    """Stable key for the two-phase ingest boundary.

    SHA-256 of (content + source_uri + tenant) so the same upload from
    the same tenant never produces duplicate pending rows.  Stored as
    ``cerid_idempotency_key`` in Chroma metadata.
    """
    blob = "\x00".join([content, source_uri, tenant])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _stage_chunks_pending(
    collection: Any,
    chunk_ids: list[str],
    idempotency_key: str,
) -> None:
    """Stamp cerid_state=pending on the chunks just written.

    ChromaDB's ``update`` method mutates metadata in place.  Called
    immediately after ``collection.add()`` so the rows are invisible to
    the retrieval gate until Neo4j commits successfully.
    """
    pending_at = utcnow_iso()
    metadatas_patch = [
        {
            "cerid_state": "pending",
            "cerid_pending_at": pending_at,
            "cerid_idempotency_key": idempotency_key,
        }
        for _ in chunk_ids
    ]
    try:
        collection.update(ids=chunk_ids, metadatas=metadatas_patch)
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error("app.services.ingestion.stage_pending", e)


def _flip_chunks_committed(collection: Any, chunk_ids: list[str]) -> None:
    """Flip cerid_state from pending → committed after Neo4j succeeds."""
    metadatas_patch = [{"cerid_state": "committed"} for _ in chunk_ids]
    try:
        collection.update(ids=chunk_ids, metadatas=metadatas_patch)
    except Exception as e:  # noqa: BLE001 — observability boundary
        # Non-fatal: the IngestRecoveryJob will forward-commit stale pending
        # rows, so a failed flip here doesn't create a permanent orphan.
        log_swallowed_error("app.services.ingestion.flip_committed", e)


def _enqueue_hype_jobs_if_enabled(
    chunk_ids: list[str],
    chunks: list[str],
    coll_name: str,
    artifact_id: str,
) -> None:
    """Enqueue a HyPEIndexingJob per committed chunk when the flag is on.

    Phase R.3.  Called inside ``ingest_content`` after Neo4j commit and
    Chroma flip succeed.  Non-blocking — each job is enqueued at LOW priority
    and executed asynchronously by the background processor.

    Lazy-imports HyPEIndexingJob so there is zero import-time cost when the
    flag is off (the default).  Silently skips if the processor queue is
    unavailable (e.g. unit tests that don't boot the full app stack).
    """
    import os
    val = os.environ.get("RETRIEVAL_HYPE_ENABLED", "false").strip().lower()
    if val not in ("true", "1", "yes", "on"):
        return

    try:
        from app.db.redis.processor_queue import enqueue_job  # noqa: PLC0415
        from app.processor.jobs.hype_indexing import HyPEIndexingJob  # noqa: PLC0415
    except ImportError as e:
        logger.debug("hype_indexer.enqueue: import failed (non-fatal): %s", e)
        return

    for chunk_id, content in zip(chunk_ids, chunks):
        try:
            payload: dict[str, Any] = {
                "chunk_id": chunk_id,
                "content": content,
                "collection_name": coll_name,
                "artifact_id": artifact_id,
            }
            job = HyPEIndexingJob(**payload)
            enqueue_job(job, payload=payload)
            logger.debug(
                "hype_indexer.enqueued chunk_id=%s artifact_id=%s",
                chunk_id, artifact_id,
            )
        except Exception as e:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.services.ingestion.hype_enqueue", e,
                context={"chunk_id": chunk_id, "artifact_id": artifact_id},
            )


def _rollback_chromadb(collection, chunk_ids: list[str]) -> None:
    """Compensating transaction: remove ChromaDB chunks when Neo4j write fails."""
    try:
        collection.delete(ids=chunk_ids)
        logger.warning(
            "Rolled back %d ChromaDB chunks after graph failure", len(chunk_ids),
        )
    except Exception as e:
        logger.error(
            "CRITICAL: ChromaDB rollback failed for %d chunks — orphaned data: %s",
            len(chunk_ids), e,
        )


def _check_duplicate(content_hash: str, domain: str) -> dict | None:
    try:
        driver = get_neo4j()
        with driver.session() as session:
            result = session.run(
                "MATCH (a:Artifact {content_hash: $hash})-[:BELONGS_TO]->(d:Domain) "
                "RETURN a.id AS id, a.filename AS filename, d.name AS domain",
                hash=content_hash,
            )
            record = result.single(strict=False)
            if record:
                return {
                    "id": record["id"],
                    "filename": record["filename"],
                    "domain": record["domain"],
                }
    except Exception as e:
        logger.warning(f"Dedup check failed (proceeding with ingest): {e}")
    return None


def _reingest_artifact(
    prev: dict, content: str, domain: str, metadata: dict | None, content_hash: str
) -> dict:
    """Update an existing artifact with new content. Preserves relationships."""
    chroma = get_chroma()
    coll_name = config.collection_name(domain)
    collection = chroma.get_or_create_collection(name=coll_name)
    artifact_id = prev["id"]

    # Delete old chunks from ChromaDB
    old_chunk_ids = json.loads(prev.get("chunk_ids", "[]") or "[]")
    if old_chunk_ids:
        try:
            collection.delete(ids=old_chunk_ids)
        except Exception as e:
            logger.warning(f"Failed to delete old chunks during re-ingest: {e}")

    # Create new chunks with contextual header
    filename = metadata.get("filename", "") if metadata else ""
    sub_cat = metadata.get("sub_category", "") if metadata else ""
    ctx_header = make_context_header(filename=filename, domain=domain, sub_category=sub_cat)
    chunks = chunk_text(
        content, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP,
        context_header=ctx_header,
    )

    # Contextual enrichment — LLM-generated situational summaries per chunk
    if config.ENABLE_CONTEXTUAL_CHUNKS:
        try:
            from core.utils.contextual import contextualize_chunks
            chunks = contextualize_chunks(chunks, content, metadata)
        except Exception as e:
            logger.warning("Contextual enrichment skipped (re-ingest): %s", e)

    # RAG C2.6 — parent-child dispatch (re-ingest path mirrors ingest_content).
    pc_active = parent_child_enabled()
    chunk_records: list[dict[str, Any]] = []
    if pc_active:
        try:
            pc_chunks = chunk_with_parents(
                content,
                artifact_id=artifact_id,
                max_tokens=config.CHUNK_MAX_TOKENS,
                overlap=config.CHUNK_OVERLAP,
                context_header=ctx_header,
            )
        except Exception as e:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.services.ingestion.chunk_with_parents_reingest", e,
            )
            pc_chunks = []
        if pc_chunks:
            for rec in pc_chunks:
                level = rec.get("chunk_level", "child")
                chunk_records.append({
                    "id": rec["chunk_id"],
                    "text": rec["text"],
                    "level": level,
                    "parent_id": (
                        rec.get("parent_chunk_id", "")
                        if level == "child"
                        else ""
                    ),
                    "retrieve_eligible": level == "child",
                })
        else:
            pc_active = False
    if not chunk_records:
        for i, c in enumerate(chunks):
            chunk_records.append({
                "id": f"{artifact_id}_chunk_{i}",
                "text": c,
                "level": "child",
                "parent_id": "",
                "retrieve_eligible": True,
            })

    base_meta = {
        "domain": domain,
        "artifact_id": artifact_id,
        "ingested_at": utcnow_iso(),
        "tenant_id": get_tenant_id(),
    }
    if metadata:
        base_meta.update(metadata)
    # Caller-supplied metadata cannot override tenant_id — that would let
    # an upload escape its own tenant scope at retrieval time.
    base_meta["tenant_id"] = get_tenant_id()

    chunk_ids = [r["id"] for r in chunk_records]
    chunk_documents = [r["text"] for r in chunk_records]
    chunk_metadatas = [
        {
            **base_meta,
            "chunk_index": i,
            "chunk_level": rec["level"],
            "parent_chunk_id": rec["parent_id"],
        }
        for i, rec in enumerate(chunk_records)
    ]
    collection.add(
        ids=chunk_ids,
        documents=chunk_documents,
        metadatas=chunk_metadatas,
    )

    bm25_ids = [r["id"] for r in chunk_records if r["retrieve_eligible"]]
    bm25_texts = [r["text"] for r in chunk_records if r["retrieve_eligible"]]
    child_chunk_ids = bm25_ids
    chunks = bm25_texts

    # BM25 index — children-only when parent-child is active.
    try:
        from core.retrieval.bm25 import index_chunks
        index_chunks(domain, bm25_ids, bm25_texts)
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.services.ingestion.bm25_index_reingest", e,
        )

    # Compute quality_score for re-ingested content
    _summary = base_meta.get("summary", "")
    _tags = base_meta.get("tags_json", "[]")
    _sub_cat = base_meta.get("sub_category", "")
    _qscore = 0.0
    if _summary and _summary != content[:200]:
        _qscore += 0.20
    try:
        _tag_list = json.loads(_tags) if _tags else []
    except (json.JSONDecodeError, TypeError):
        _tag_list = []
    if _tag_list:
        _qscore += 0.15
    if len(chunks) > 1:
        _qscore += 0.15
    if len(content) > 500:
        _qscore += 0.15
    if domain:
        _qscore += 0.10
    if _sub_cat and _sub_cat != config.DEFAULT_SUB_CATEGORY:
        _qscore += 0.10
    _qscore += 0.15  # dedup passed
    quality_score = round(min(_qscore, 1.0), 2)

    # Update Neo4j artifact (preserves relationships)
    try:
        graph.update_artifact(
            get_neo4j(),
            artifact_id=artifact_id,
            keywords_json=base_meta.get("keywords_json", "[]"),
            summary=base_meta.get("summary", content[:200]),
            chunk_count=len(child_chunk_ids),
            chunk_ids_json=json.dumps(child_chunk_ids),
            content_hash=content_hash,
            quality_score=quality_score,
        )
    except Exception as e:
        logger.error(f"Failed to update artifact in Neo4j during re-ingest: {e}")

    logger.info(f"Re-ingested artifact {artifact_id[:8]} ({base_meta.get('filename', '?')})")
    return {
        "status": "updated",
        "artifact_id": artifact_id,
        "domain": domain,
        "chunks": len(chunks),
        "timestamp": utcnow_iso(),
    }


# ── Public service functions ───────────────────────────────────────────────────

def ingest_content(
    content: str,
    domain: str = "general",
    metadata: dict[str, Any] | None = None,
    *,
    skip_quality: bool = False,
    pre_chunked: list[dict[str, Any]] | None = None,
) -> dict:
    """Core ingest path. Called by REST endpoints, agents, and MCP tool dispatcher.

    When ``skip_quality`` is True the weighted 4-dimension quality score is
    skipped (neutral 0.5 stored) — used by wizard / bulk paths that don't
    have the summary/keywords the quality function expects. The curator
    agent can re-score later when artifact metadata is enriched.

    ``pre_chunked`` (Workstream E Phase 2b wire-in) accepts an already-
    dispatched chunk list of ``[{"text": str, "metadata": dict}, ...]``
    from :func:`core.ingest.dispatch.layout_aware_parse`. When supplied,
    the inline ``chunk_text`` + ``contextualize_chunks`` step is skipped
    and the per-chunk metadata (column_headers, heading_path,
    file:start_line:end_line, etc.) is merged into ChromaDB metadata so
    retrieval can filter by structural shape. ``content`` is still the
    canonical artifact text used for content_hash / AI categorization /
    Neo4j summary — ``pre_chunked`` only overrides the chunk-write step.
    """
    chroma = get_chroma()
    coll_name = config.collection_name(domain)
    collection = chroma.get_or_create_collection(name=coll_name)

    artifact_id = str(uuid.uuid4())
    content_hash = _content_hash(content)

    existing = _check_duplicate(content_hash, domain)
    if existing:
        fname = (metadata or {}).get("filename", "?")
        logger.info(
            f"Duplicate detected: '{fname}' matches "
            f"existing artifact {existing['id']} ('{existing['filename']}' in {existing['domain']})"
        )
        return {
            "status": "duplicate",
            "artifact_id": existing["id"],
            "domain": existing["domain"],
            "chunks": 0,
            "timestamp": utcnow_iso(),
            "duplicate_of": existing["filename"],
        }

    # Semantic deduplication (Pro feature)
    near_dup = None
    try:
        from utils.features import is_feature_enabled

        if is_feature_enabled("semantic_dedup"):
            from utils.dedup import check_semantic_duplicate

            near_dup = check_semantic_duplicate(
                text=content,
                domain=domain,
                chroma_client=get_chroma(),
            )
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.services.ingestion.semantic_dedup", e,
        )

    # Re-ingestion check: same filename, different content
    fname = (metadata or {}).get("filename", "text_input")
    if fname != "text_input":
        try:
            prev = graph.find_artifact_by_filename(get_neo4j(), fname, domain)
            if prev and prev["content_hash"] != content_hash:
                return _reingest_artifact(prev, content, domain, metadata, content_hash)
        except Exception as e:
            logger.warning(f"Re-ingest check failed (proceeding as new): {e}")

    # Workstream E Phase 2b wire-in: when caller supplies layout-aware
    # pre-chunked text + per-chunk metadata, skip the token-chunker and
    # contextual-enrichment passes. The pre-chunked path already shaped
    # each row / section / function as its own chunk with structural
    # metadata that downstream retrieval depends on.
    pre_chunk_metadatas: list[dict[str, Any]] = []
    # RAG Cycle C2.1 — pre-chunked input may contain zero-text
    # ``WikilinkEdge`` chunks alongside the real text chunks. Those are
    # metadata-only edge markers consumed by the post-create graph commit
    # below; they must NOT be sent to ChromaDB (would pollute retrieval
    # with empty-document rows) and must NOT participate in chunk-id
    # assignment (the chunk_id we hand the graph layer points at the
    # text chunk that produced the wikilink).
    wikilink_edge_chunks: list[dict[str, Any]] = []
    # RAG Cycle C2.2 — YAML frontmatter dict extracted from the first
    # text chunk (markdown parser stamps it on the first MarkdownSection
    # element).  ``aliases`` flows into resolve_pending_artifacts;
    # ``tags`` merges into the Neo4j Tag taxonomy; reserved scalars +
    # ``cerid:*`` custom keys land as Artifact node properties.
    frontmatter: dict[str, Any] = {}
    if pre_chunked:
        text_pre_chunked = [
            c for c in pre_chunked
            if c.get("metadata", {}).get("element_type") != "WikilinkEdge"
        ]
        wikilink_edge_chunks = [
            c for c in pre_chunked
            if c.get("metadata", {}).get("element_type") == "WikilinkEdge"
        ]
        chunks = [c["text"] for c in text_pre_chunked]
        pre_chunk_metadatas = [
            dict(c.get("metadata", {})) for c in text_pre_chunked
        ]
        # Lift frontmatter off the first text chunk so it doesn't get
        # written into every chunk's ChromaDB metadata (the dict lives on
        # the Artifact node, not on each chunk).  We still leave the
        # JSON-serialised form on chunk 0 so consumers that want per-doc
        # frontmatter from chroma retrieval can read it back.
        if pre_chunk_metadatas:
            fm_json = pre_chunk_metadatas[0].pop("frontmatter_json", None)
            if fm_json:
                try:
                    parsed_fm = json.loads(fm_json)
                    if isinstance(parsed_fm, dict):
                        frontmatter = parsed_fm
                except (json.JSONDecodeError, TypeError) as e:
                    # Frontmatter JSON came from our own parser one
                    # call frame up, so a decode failure here is a real
                    # bug (not user input).  Surface at warn level.
                    logger.warning("frontmatter_json decode failed: %s", e)
                # Re-attach the JSON so chunk 0's metadata still carries it
                # for downstream consumers (search filters etc.).
                pre_chunk_metadatas[0]["frontmatter_json"] = fm_json
    else:
        fname_for_header = (metadata or {}).get("filename", "")
        sub_cat_for_header = (metadata or {}).get("sub_category", "")
        ctx_header = make_context_header(
            filename=fname_for_header, domain=domain, sub_category=sub_cat_for_header,
        )
        chunks = chunk_text(
            content, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP,
            context_header=ctx_header,
        )

        # Contextual enrichment — LLM-generated situational summaries per chunk
        if config.ENABLE_CONTEXTUAL_CHUNKS:
            try:
                from core.utils.contextual import contextualize_chunks
                chunks = contextualize_chunks(chunks, content, metadata)
            except Exception as e:
                logger.warning("Contextual enrichment skipped: %s", e)

    # ── RAG C2.6 — parent-child chunk dispatch ────────────────────────────
    # When ``ENABLE_PARENT_CHILD_RETRIEVAL`` is on AND the caller didn't
    # supply pre-chunked layout-aware input, split each parent chunk into
    # smaller children. Both classes are written to Chroma; retrieval ranks
    # against children and substitutes parent text at read time. When the
    # flag is off, every chunk is labelled ``chunk_level="child"`` with an
    # empty ``parent_chunk_id`` so the metadata shape is uniform and the
    # query-side filter doesn't need a runtime branch.
    pc_active = parent_child_enabled() and not pre_chunked
    # ``chunk_records`` is the canonical structure for the rest of this
    # function: a list of (id, text, level, parent_id, retrieve_eligible).
    # retrieve_eligible flags rows that participate in BM25 + HyPE indexing
    # and that count toward the artifact's chunk_count (children-only when
    # parent-child is active; every row otherwise).
    chunk_records: list[dict[str, Any]] = []
    if pc_active:
        try:
            pc_chunks = chunk_with_parents(
                content,
                artifact_id=artifact_id,
                max_tokens=config.CHUNK_MAX_TOKENS,
                overlap=config.CHUNK_OVERLAP,
                context_header=ctx_header,
            )
        except Exception as e:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.services.ingestion.chunk_with_parents", e,
            )
            pc_chunks = []
        if pc_chunks:
            for rec in pc_chunks:
                level = rec.get("chunk_level", "child")
                chunk_records.append({
                    "id": rec["chunk_id"],
                    "text": rec["text"],
                    "level": level,
                    "parent_id": (
                        rec.get("parent_chunk_id", "")
                        if level == "child"
                        else ""
                    ),
                    "retrieve_eligible": level == "child",
                })
        else:
            # Fall through to flat if the helper returned nothing.
            pc_active = False
    if not chunk_records:
        for i, c in enumerate(chunks):
            chunk_records.append({
                "id": f"{artifact_id}_chunk_{i}",
                "text": c,
                "level": "child",
                "parent_id": "",
                "retrieve_eligible": True,
            })

    ingested_at = utcnow_iso()
    base_meta = {
        "domain": domain,
        "artifact_id": artifact_id,
        "ingested_at": ingested_at,
        "tenant_id": get_tenant_id(),
    }
    if metadata:
        base_meta.update(metadata)
    # Caller-supplied metadata cannot override tenant_id — that would let
    # an upload escape its own tenant scope at retrieval time.
    base_meta["tenant_id"] = get_tenant_id()

    # Propagate client_source for provenance tracking
    if metadata and metadata.get("client_source"):
        base_meta["client_source"] = metadata["client_source"]

    # RAG Cycle C2.2 — fold frontmatter ``tags`` into ``tags_json`` so
    # the Neo4j Tag taxonomy wiring inside ``create_artifact`` sees the
    # union of caller-supplied tags + frontmatter tags.  Existing tags
    # win on duplicates (case-insensitive) — frontmatter only adds.
    fm_tags_raw = frontmatter.get("tags")
    if isinstance(fm_tags_raw, list) and fm_tags_raw:
        existing_tags_json = base_meta.get("tags_json", "[]") or "[]"
        existing_tags: list[Any]
        try:
            decoded = json.loads(existing_tags_json) if existing_tags_json else []
            existing_tags = decoded if isinstance(decoded, list) else []
        except (json.JSONDecodeError, TypeError):
            existing_tags = []
        existing_lower = {
            t.strip().lower() for t in existing_tags if isinstance(t, str) and t.strip()
        }
        merged_tags: list[str] = [
            t for t in existing_tags if isinstance(t, str)
        ]
        for tag in fm_tags_raw:
            if not isinstance(tag, str):
                continue
            clean = tag.strip().lower()
            if not clean or clean in existing_lower:
                continue
            merged_tags.append(clean)
            existing_lower.add(clean)
        base_meta["tags_json"] = json.dumps(merged_tags)

    # ``created`` / ``updated`` from frontmatter override the
    # system-set ingest timestamp so an Obsidian note that was created
    # in 2023 doesn't claim 2026 in the Artifact node.  YAML may emit
    # datetime objects — coerce to ISO string so Neo4j's property type
    # stays consistent across all artifacts.
    if frontmatter.get("created"):
        base_meta["created_at"] = _coerce_frontmatter_timestamp(
            frontmatter["created"],
        )
    if frontmatter.get("updated"):
        base_meta["updated_at"] = _coerce_frontmatter_timestamp(
            frontmatter["updated"],
        )

    # Tag near-duplicate in metadata
    if near_dup:
        base_meta["near_duplicate_of"] = near_dup["artifact_id"]
        base_meta["near_duplicate_similarity"] = str(near_dup["similarity"])

    # Phase O.1 — idempotency key: SHA-256(content + source_uri + tenant).
    # source_uri comes from metadata["filename"] if available.
    _source_uri = base_meta.get("filename", base_meta.get("source_uri", ""))
    _tenant = base_meta["tenant_id"]
    idempotency_key = _idempotency_key(content, _source_uri, _tenant)

    chunk_ids = [r["id"] for r in chunk_records]
    chunk_documents = [r["text"] for r in chunk_records]
    if pre_chunk_metadatas:
        # Per-chunk structural metadata (column_headers, heading_path,
        # file:start_line:end_line) merges over base_meta. tenant_id is
        # re-asserted last so a malformed parser metadata entry can't
        # escape its own tenant scope. Values are JSON-coerced because
        # ChromaDB rejects list/dict metadata.
        chunk_metadatas = []
        for i, extras in enumerate(pre_chunk_metadatas):
            merged: dict[str, Any] = {**base_meta}
            for k, v in extras.items():
                merged[k] = _coerce_chroma_meta(v)
            merged["chunk_index"] = i
            merged["tenant_id"] = base_meta["tenant_id"]
            # RAG C2.6 — pre-chunked rows are all leaf chunks. Stamp the
            # uniform parent-child fields so query-side filters apply
            # consistently regardless of how the chunks were produced.
            merged["chunk_level"] = "child"
            merged["parent_chunk_id"] = ""
            chunk_metadatas.append(merged)
    else:
        chunk_metadatas = []
        for i, rec in enumerate(chunk_records):
            md = {
                **base_meta,
                "chunk_index": i,
                "chunk_level": rec["level"],
                "parent_chunk_id": rec["parent_id"],
            }
            chunk_metadatas.append(md)
    collection.add(
        ids=chunk_ids,
        documents=chunk_documents,
        metadatas=chunk_metadatas,
    )

    # Phase O.1 — stage: mark all chunks as pending so the retrieval gate
    # excludes them until the Neo4j commit confirms the write.
    _stage_chunks_pending(collection, chunk_ids, idempotency_key)

    # RAG C2.6 — BM25 indexes only retrieve-eligible chunks (children in
    # parent-child mode, every chunk otherwise). Parents are read-time
    # context substitutions, not retrieval targets.
    bm25_ids = [r["id"] for r in chunk_records if r["retrieve_eligible"]]
    bm25_texts = [r["text"] for r in chunk_records if r["retrieve_eligible"]]
    # The artifact's stored chunk_count and chunk_ids_json reflect the
    # retrieve-eligible set so graph_expand_results and curator paths
    # continue to drive against retrievable rows.
    child_chunk_ids = bm25_ids
    child_chunk_texts = bm25_texts
    # ``chunks`` is the legacy list used downstream for quality scoring
    # and the response payload. Keep it pointing at the retrieve-eligible
    # set so quality scoring and per-artifact stats don't double-count
    # parents.
    chunks = child_chunk_texts

    # Index for BM25 hybrid search
    try:
        from core.retrieval.bm25 import index_chunks
        index_chunks(domain, bm25_ids, bm25_texts)
    except Exception as e:
        logger.warning(f"BM25 indexing failed (non-blocking): {e}")

    # Compute quality_score using weighted 4-dimension formula (skip in fast
    # paths where summary/keywords haven't been populated — curator re-scores
    # later; neutral 0.5 lets retrieval work in the meantime).
    if skip_quality:
        quality_score = 0.5
    else:
        from core.utils.quality import compute_quality_score as _compute_quality

        quality_score = _compute_quality(
            summary=base_meta.get("summary", ""),
            keywords=base_meta.get("keywords_json", "[]"),
            tags=base_meta.get("tags_json", "[]"),
            sub_category=base_meta.get("sub_category", ""),
            default_sub_category=config.DEFAULT_SUB_CATEGORY,
            ingested_at=base_meta.get("ingested_at"),
        )

    artifact_created = False
    try:
        driver = get_neo4j()
        graph.create_artifact(
            driver,
            artifact_id=artifact_id,
            filename=base_meta.get("filename", "text_input"),
            domain=domain,
            keywords_json=base_meta.get("keywords_json", "[]"),
            summary=base_meta.get("summary", content[:200]),
            chunk_count=len(child_chunk_ids),
            chunk_ids_json=json.dumps(child_chunk_ids),
            content_hash=content_hash,
            sub_category=base_meta.get("sub_category", config.DEFAULT_SUB_CATEGORY),
            tags_json=base_meta.get("tags_json", "[]"),
            quality_score=quality_score,
            client_source=base_meta.get("client_source", ""),
        )
        artifact_created = True
        # Phase O.1 — commit: flip Chroma chunks from pending → committed.
        # Non-fatal if this flip fails; IngestRecoveryJob will forward-commit.
        _flip_chunks_committed(collection, chunk_ids)

        # RAG Cycle C2.2 — frontmatter → Artifact properties.
        # Reserved scalars (status, cssclass, source) + ``cerid:*`` custom
        # keys (rewritten as ``cerid_*`` because Neo4j property names
        # can't contain colons) + the overridden created/updated
        # timestamps go on the Artifact node so downstream querying can
        # filter by them.  ``tags`` was already merged into ``tags_json``
        # before create_artifact, so it doesn't need a second pass.
        # ``aliases`` is consumed by resolve_pending_artifacts below; we
        # also stamp it on the Artifact for traceability.
        if frontmatter:
            extra_props = _frontmatter_to_artifact_props(
                frontmatter,
                created_override=base_meta.get("created_at"),
                updated_override=base_meta.get("updated_at"),
            )
            if extra_props:
                try:
                    graph.set_artifact_properties(
                        driver=driver,
                        artifact_id=artifact_id,
                        properties=extra_props,
                    )
                except Exception as e:  # noqa: BLE001 — observability boundary
                    log_swallowed_error(
                        "app.services.ingestion.frontmatter_props", e,
                    )

        # Phase R.3 — HyPE: enqueue background indexing job per chunk.
        # Only fires when RETRIEVAL_HYPE_ENABLED=true (off by default until
        # eval gate is cleared).  Non-blocking — HyPE runs asynchronously
        # in the processor queue at LOW priority.
        # RAG C2.6 — HyPE indexes child granularity to match BM25 + vector
        # ranking precision. Parent chunks are read-time only.
        _enqueue_hype_jobs_if_enabled(
            chunk_ids=child_chunk_ids,
            chunks=child_chunk_texts,
            coll_name=coll_name,
            artifact_id=artifact_id,
        )
    except Exception as e:
        err_msg = str(e).lower()
        if "constraint" in err_msg and "content_hash" in err_msg:
            logger.info(f"Concurrent duplicate detected via constraint: {base_meta.get('filename', '?')}")
            _rollback_chromadb(collection, chunk_ids)
            return {
                "status": "duplicate",
                "artifact_id": artifact_id,
                "domain": domain,
                "chunks": 0,
                "timestamp": utcnow_iso(),
                "duplicate_of": "(concurrent)",
            }
        # Phase O.1 — on Neo4j failure, leave Chroma rows in pending state.
        # The IngestRecoveryJob will scan for stale pending rows and either
        # roll-forward (if Neo4j becomes available) or purge after 2 retries.
        logger.error(
            "Neo4j artifact creation failed (chunks staged as pending for recovery): %s", e
        )
        return {
            "status": "error",
            "artifact_id": artifact_id,
            "domain": domain,
            "chunks": 0,
            "timestamp": utcnow_iso(),
            "error": f"Graph storage failed: {e}",
        }

    try:
        cache.log_event(
            get_redis(),
            event_type="ingest",
            artifact_id=artifact_id,
            domain=domain,
            filename=base_meta.get("filename", "text_input"),
        )
    except Exception as e:
        logger.error(f"Redis log failed: {e}")

    # Discover and create relationships with existing artifacts
    relationships_created = 0
    if artifact_created:
        try:
            relationships_created = graph.discover_relationships(
                driver=get_neo4j(),
                artifact_id=artifact_id,
                filename=base_meta.get("filename", "text_input"),
                domain=domain,
                keywords_json=base_meta.get("keywords_json", "[]"),
                content=content[:5000],  # limit content scan for performance
            )
        except Exception as e:
            logger.warning(f"Relationship discovery failed (non-blocking): {e}")

    # RAG Cycle C2.1 — wikilink edge commits (mirror of the
    # EmailThreadEdge → REPLIES_TO contract). For each zero-text
    # ``WikilinkEdge`` chunk emitted by the markdown chunker, write a
    # ``WIKILINKS_TO`` or ``EMBEDS`` Neo4j edge (or a pending edge to a
    # ``PendingArtifact`` placeholder when the target hasn't been ingested
    # yet). Then sweep for any pending artifacts that this new artifact
    # resolves — both calls are wrapped in observability boundaries
    # because edge-creation must never break ingestion.
    if artifact_created:
        # Resolve against child_chunk_ids: the chunker emits indices into
        # the flat retrieval-eligible sequence, which is child-only when
        # parent-child is enabled. Indexing into ``chunk_ids`` (which has
        # parents interleaved when pc is on) would point at a parent row
        # — not retrieval-visible — for any wikilink past the first
        # parent's position. Caught in C2 audit.
        retrieval_chunk_ids = child_chunk_ids if child_chunk_ids else chunk_ids
        for edge_chunk in wikilink_edge_chunks:
            edge_meta = edge_chunk.get("metadata", {})
            try:
                source_idx_str = str(edge_meta.get("wikilink_source_chunk_idx", "0"))
                source_idx = int(source_idx_str)
                # Guard against an out-of-range chunk index (shouldn't
                # happen but the chunker is across the import boundary).
                if 0 <= source_idx < len(retrieval_chunk_ids):
                    source_chunk_id = retrieval_chunk_ids[source_idx]
                else:
                    source_chunk_id = (
                        retrieval_chunk_ids[0] if retrieval_chunk_ids
                        else f"{artifact_id}_chunk_0"
                    )
                target = str(edge_meta.get("wikilink_target", "")).strip()
                if not target:
                    continue
                is_embed = str(edge_meta.get("wikilink_is_embed", "false")) == "true"
                graph.write_wikilink_edge(
                    driver=get_neo4j(),
                    source_artifact_id=artifact_id,
                    target_name=target,
                    is_embed=is_embed,
                    source_chunk_id=source_chunk_id,
                    alias=str(edge_meta.get("wikilink_alias", "")),
                    heading=str(edge_meta.get("wikilink_heading", "")),
                )
            except Exception as e:  # noqa: BLE001 — observability boundary
                log_swallowed_error(
                    "app.services.ingestion.wikilink_edge", e,
                )
        try:
            # RAG Cycle C2.2 — frontmatter ``aliases`` thread.  A note
            # whose frontmatter lists ``aliases: [Foo]`` should resolve
            # any inbound ``[[Foo]]`` link to this artifact even if its
            # filename stem is different.
            fm_aliases_raw = frontmatter.get("aliases") if frontmatter else None
            alias_list: list[str] | None = None
            if isinstance(fm_aliases_raw, list):
                alias_list = [
                    a for a in fm_aliases_raw
                    if isinstance(a, str) and a.strip()
                ]
                if not alias_list:
                    alias_list = None
            graph.resolve_pending_artifacts(
                driver=get_neo4j(),
                artifact_id=artifact_id,
                filename=base_meta.get("filename", "text_input"),
                aliases=alias_list,
            )
        except Exception as e:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.services.ingestion.wikilink_resolve_pending", e,
            )

    # Fire webhook notification
    try:
        from utils.webhooks import notify_ingestion_complete
        asyncio.get_running_loop().create_task(
            notify_ingestion_complete(artifact_id, domain, base_meta.get("filename", "text_input"), len(chunks))
        )
    except RuntimeError:
        pass  # no running loop (e.g. sync context) — webhook skipped
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.services.ingestion.webhook_notify", e,
        )

    # Surface related artifacts in response
    related = []
    if relationships_created > 0:
        try:
            found = graph.find_related_artifacts(
                get_neo4j(), artifact_ids=[artifact_id], depth=1, max_results=5,
            )
            related = [
                {"id": r["id"], "filename": r["filename"], "domain": r["domain"],
                 "relationship_type": r.get("relationship_type", "")}
                for r in found
            ]
        except Exception as e:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.services.ingestion.related_lookup", e,
            )

    result = {
        "status": "success",
        "artifact_id": artifact_id,
        "domain": domain,
        "chunks": len(chunks),
        "relationships_created": relationships_created,
        "related": related,
        "timestamp": utcnow_iso(),
    }

    # Include near-duplicate info if detected
    if near_dup:
        result["near_duplicate_of"] = {
            "artifact_id": near_dup["artifact_id"],
            "filename": near_dup["filename"],
            "similarity": near_dup["similarity"],
        }

    return result


# ---------------------------------------------------------------------------
# RAG Cycle C2.4 — recursive email-attachment ingestion
# ---------------------------------------------------------------------------

# Source type stamped on attachment artifacts so downstream filters can
# distinguish them from primary email ingestions.
_EMAIL_ATTACHMENT_SOURCE_TYPE = "email_attachment"


def _ingest_single_attachment(
    *,
    blob: Any,  # core.ingest.attachments.AttachmentBlob — Any to avoid eager import
    parent_artifact_id: str,
    parent_domain: str,
    parent_meta: dict[str, Any],
) -> dict[str, Any] | None:
    """Synchronously ingest one attachment blob; return a summary or None.

    Steps:

    1. Magic-byte sniff the bytes against the filename extension.
       Mismatch → log + skip (returns None).
    2. Dispatch to the parser registry by extension. Unsupported
       extension → log + skip.
    3. Write the bytes to ``tempfile.NamedTemporaryFile`` (the existing
       parser interface is file-path-only), parse, then delete.
    4. Stamp metadata: ``source_type='email_attachment'``,
       ``parent_artifact_id``, and the parent email's identifying fields
       (Message-ID, sender, subject) where present in ``parent_meta``.
    5. Call ``ingest_content`` directly (skips ``validate_file_path``).
    6. Write the ``HAS_ATTACHMENT`` Neo4j edge — both for a fresh ingest
       and for the dedup-hit case, so an attachment that's already
       ingested under a different email still links to this email.

    Errors propagate to the caller (which logs via
    ``log_swallowed_error`` and continues with the next attachment).
    """
    import os
    import tempfile

    from app.parsers import parse_file as _parse_file
    from app.parsers.email import _SKIP_NESTED_ATTACHMENTS
    from app.parsers.magic_bytes import magic_bytes_match
    from app.parsers.registry import PARSER_REGISTRY
    from utils.metadata import extract_metadata_minimal

    suffix = Path(blob.filename).suffix.lower()
    if not suffix or suffix not in PARSER_REGISTRY:
        logger.info(
            "email_attachment skipped (unsupported extension): "
            "parent=%s attachment=%s suffix=%r",
            parent_artifact_id[:8], blob.filename, suffix,
        )
        return {
            "filename": blob.filename,
            "status": "skipped",
            "reason": f"unsupported extension {suffix!r}",
        }

    ok, detected = magic_bytes_match(suffix, blob.content_bytes)
    if not ok:
        logger.warning(
            "email_attachment_magic_mismatch: parent=%s attachment=%s "
            "suffix=%s detected=%r",
            parent_artifact_id[:8], blob.filename, suffix, detected,
        )
        # Counter hook — observability dashboards can sum log lines with
        # this stable prefix.
        log_swallowed_error(
            "app.services.ingestion.email_attachment_magic_mismatch",
            ValueError(
                f"magic-byte mismatch: ext={suffix} detected={detected!r}"
            ),
            context={
                "parent_artifact_id": parent_artifact_id,
                "attachment_filename": blob.filename,
                "declared_suffix": suffix,
                "detected_extension": detected,
            },
        )
        return {
            "filename": blob.filename,
            "status": "skipped",
            "reason": "magic-byte mismatch",
        }

    # Write to a temp file so the existing file-path parser interface
    # works unchanged. The file is cleaned up in the finally block even
    # on parser failure.
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="email_attach_")
    try:
        with os.fdopen(tmp_fd, "wb") as fh:
            fh.write(blob.content_bytes)

        # Cycle-prevention: if this attachment is itself an .eml, the
        # parser sees the ContextVar and emits body text + listing but
        # NO _attachments — so we won't recurse a second level deep.
        token = _SKIP_NESTED_ATTACHMENTS.set(True)
        try:
            parsed = _parse_file(tmp_path)
        finally:
            _SKIP_NESTED_ATTACHMENTS.reset(token)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError as e:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.services.ingestion.email_attachment_tmp_cleanup", e,
                context={"tmp_path": tmp_path},
            )

    text = parsed.get("text", "")
    if not isinstance(text, str) or not text.strip():
        logger.info(
            "email_attachment skipped (no extractable text): "
            "parent=%s attachment=%s",
            parent_artifact_id[:8], blob.filename,
        )
        return {
            "filename": blob.filename,
            "status": "skipped",
            "reason": "no extractable text",
        }

    # Minimal metadata pass — attachments inherit the parent's domain
    # so the AI-categoriser is intentionally NOT invoked here (the
    # locked design: re-classification can run later if the curator
    # wants to). Filename-derived keywords give retrieval something to
    # match on without paying the spaCy/tiktoken cost per attachment.
    attachment_meta = extract_metadata_minimal(
        text, blob.filename, parent_domain,
    )
    attachment_meta["filename"] = blob.filename
    attachment_meta["domain"] = parent_domain
    attachment_meta["file_type"] = parsed.get(
        "file_type", attachment_meta.get("file_type", ""),
    )
    if parsed.get("page_count") is not None:
        attachment_meta["page_count"] = parsed["page_count"]

    # C2.4 — parentage stamps on the attachment artifact's metadata.
    # ``source_type`` makes the attachment surfaceable in retrieval
    # filters; ``parent_artifact_id`` is the explicit back-pointer used
    # by graph queries that walk HAS_ATTACHMENT in either direction.
    attachment_meta["source_type"] = _EMAIL_ATTACHMENT_SOURCE_TYPE
    attachment_meta["parent_artifact_id"] = parent_artifact_id
    for parent_key, attach_key in (
        ("message_id", "parent_message_id"),
        ("from", "parent_email_from"),
        ("subject", "parent_email_subject"),
    ):
        val = parent_meta.get(parent_key)
        if val:
            # Cap each header field at a sane width so a malicious /
            # mis-formatted email can't push a 1 MB Subject header into
            # ChromaDB metadata.
            attachment_meta[attach_key] = str(val)[:512]

    ingest_result = ingest_content(
        text,
        parent_domain,
        attachment_meta,
        skip_quality=True,  # attachments use neutral quality — curator can re-score
    )

    child_artifact_id = ingest_result.get("artifact_id")
    status = ingest_result.get("status")
    if not child_artifact_id or status == "error":
        logger.warning(
            "email_attachment ingest failed: parent=%s attachment=%s status=%s",
            parent_artifact_id[:8], blob.filename, status,
        )
        return {
            "filename": blob.filename,
            "status": status or "error",
            "reason": ingest_result.get("error", "ingest failed"),
        }

    # Write the HAS_ATTACHMENT edge — both for new and dedup'd
    # attachments. The dedup case (status == "duplicate") still creates
    # the edge so a shared attachment links to BOTH parent emails.
    try:
        graph.write_has_attachment(
            driver=get_neo4j(),
            parent_id=parent_artifact_id,
            child_id=str(child_artifact_id),
            filename=blob.filename,
            content_type=blob.content_type,
        )
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.services.ingestion.email_attachment_edge", e,
            context={
                "parent_artifact_id": parent_artifact_id,
                "child_artifact_id": str(child_artifact_id),
                "attachment_filename": blob.filename,
            },
        )

    return {
        "filename": blob.filename,
        "status": status,
        "artifact_id": str(child_artifact_id),
        "content_type": blob.content_type,
    }


async def _ingest_email_attachments(
    *,
    attachments: list[Any],
    parent_artifact_id: str,
    parent_domain: str,
    parent_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    """Iterate parsed attachments; ingest each off-thread.

    Each blob is processed sequentially under a single ``to_thread`` call
    so the parent ingest's response shape is preserved. We catch every
    per-attachment exception so one bad attachment doesn't abort the
    rest of the batch.
    """
    summaries: list[dict[str, Any]] = []
    for blob in attachments:
        try:
            summary = await asyncio.to_thread(
                _ingest_single_attachment,
                blob=blob,
                parent_artifact_id=parent_artifact_id,
                parent_domain=parent_domain,
                parent_meta=parent_meta,
            )
        except Exception as e:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.services.ingestion.email_attachment", e,
                context={
                    "parent_artifact_id": parent_artifact_id,
                    "attachment_filename": getattr(blob, "filename", "?"),
                },
            )
            summaries.append({
                "filename": getattr(blob, "filename", "?"),
                "status": "error",
                "reason": str(e),
            })
            continue
        if summary is not None:
            summaries.append(summary)
    return summaries


async def ingest_file(
    file_path: str,
    domain: str = "",
    sub_category: str = "",
    tags: str = "",
    categorize_mode: str = "",
    client_source: str = "",
    *,
    skip_metadata: bool = False,
    skip_quality: bool = False,
    extra_metadata: dict[str, Any] | None = None,
) -> dict:
    """Parse a file, extract metadata, optionally AI-categorize, chunk, and store.

    ``skip_metadata`` swaps the NLP-heavy ``extract_metadata()`` (spaCy NER
    + tiktoken) for the fast ``extract_metadata_minimal()`` fallback used
    by the setup wizard — trades keyword/summary quality for sub-100ms
    latency. ``skip_quality`` is threaded into ``ingest_content`` and skips
    the 4-dimension quality scorer. Both flags are opt-in and default False;
    the frontend sets them on the wizard's "Try It Out" ingest so the user
    isn't waiting for metadata extraction before their first query.

    ``extra_metadata`` (Phase 8a wire-in) lets callers stamp arbitrary
    string-valued metadata onto every chunk written to chromadb. The
    knowledge-pack harness uses this to attach pack provenance:
    ``source_url``, ``license_spdx``, ``retrieved_at``, ``recipe_rev``,
    ``adapter``. Caller-supplied values are merged AFTER the
    NLP-extracted metadata so they can't be overwritten by a noisy
    summary/keyword pass; ``tenant_id`` is still re-asserted at the
    chromadb-write boundary (in ``ingest_content``) so the
    ``extra_metadata`` channel can never be used to escape a tenant.
    """
    validate_file_path(file_path)
    filename = Path(file_path).name

    # Workstream E Phase 2b wire-in: when ENABLE_LAYOUT_AWARE_PARSING is on,
    # supported extensions (.csv, .md, .markdown, .py) route through
    # core/ingest/parsers/ so each row / section / function becomes its own
    # chunk with structural metadata. Falls through to the legacy parse_file
    # path on any failure or unsupported extension.
    pre_chunked: list[dict[str, Any]] | None = None
    parsed: dict[str, Any]
    if config.ENABLE_LAYOUT_AWARE_PARSING:
        from core.ingest.dispatch import layout_aware_parse
        layout_result = await asyncio.to_thread(layout_aware_parse, file_path)
        if layout_result is not None:
            raw_text, pre_chunked = layout_result
            ext = Path(file_path).suffix.lstrip(".").lower()
            parsed = {
                "text": raw_text,
                "file_type": ext,
                "page_count": None,
                "parser": "layout_aware",
            }
        else:
            # Run sync parser in thread pool to avoid blocking the event loop
            parsed = await asyncio.to_thread(parse_file, file_path)
    else:
        # Run sync parser in thread pool to avoid blocking the event loop
        # (CPU-bound: PDF/DOCX parsing can take 100ms–2s per file)
        parsed = await asyncio.to_thread(parse_file, file_path)
    text = parsed["text"]
    if skip_metadata:
        meta = extract_metadata_minimal(text, filename, domain or config.DEFAULT_DOMAIN)
    else:
        meta = extract_metadata(text, filename, domain or config.DEFAULT_DOMAIN)
    mode = categorize_mode or (
        "manual" if domain and domain in config.DOMAINS else config.CATEGORIZE_MODE
    )
    if mode != "manual" and not domain:
        ai_result = await ai_categorize(text, filename, mode)
        if ai_result.get("suggested_domain"):
            domain = ai_result["suggested_domain"]
            meta["ai_categorized"] = "true"
            meta["categorize_mode"] = mode
        if ai_result.get("keywords"):
            meta["keywords_json"] = json.dumps(ai_result["keywords"])
        if ai_result.get("summary"):
            meta["summary"] = ai_result["summary"]
        # Sub-category and tags from AI
        if ai_result.get("sub_category") and not sub_category:
            sub_category = ai_result["sub_category"]
        if ai_result.get("tags") and not tags:
            tags = json.dumps(ai_result["tags"])
    if not domain or domain not in config.DOMAINS:
        domain = config.DEFAULT_DOMAIN
    meta["domain"] = domain
    meta["sub_category"] = sub_category or config.DEFAULT_SUB_CATEGORY
    # Normalize tags: accept JSON array string or comma-separated
    if tags:
        if tags.startswith("["):
            meta["tags_json"] = tags
        else:
            meta["tags_json"] = json.dumps([t.strip().lower() for t in tags.split(",") if t.strip()])
    meta["file_type"] = parsed.get("file_type", "")
    if parsed.get("page_count") is not None:
        meta["page_count"] = parsed["page_count"]
    if client_source:
        meta["client_source"] = client_source
    # C2.4 — stamp ``source_type="email"`` on .eml/.mbox parents so the
    # ``(:Artifact {source_type: "email"})-[:HAS_ATTACHMENT]->(:Artifact)``
    # contract from the locked design is discoverable in graph queries
    # without re-parsing the artifact body.
    if parsed.get("file_type") in ("eml", "mbox"):
        meta["source_type"] = "email"
    # Phase 8a: pack-provenance fields — kept as plain strings so they
    # round-trip through chromadb metadata cleanly. Caller-supplied
    # ``extra_metadata`` is *appended* (not overwritten) so a malformed
    # NLP-extract pass can't smuggle a different source_url into the
    # provenance chain.
    if extra_metadata:
        for k, v in extra_metadata.items():
            if v is None:
                continue
            meta[str(k)] = str(v)
    # Run sync ingest_content in thread pool to avoid blocking the event loop
    # (I/O-bound: Neo4j, ChromaDB, Redis writes + CPU-bound tiktoken chunking).
    # Forward layout-aware pre_chunked through so per-chunk structural
    # metadata (column_headers, heading_path, ...) reaches ChromaDB.
    result = await asyncio.to_thread(
        ingest_content,
        text, domain, meta,
        skip_quality=skip_quality,
        pre_chunked=pre_chunked,
    )
    result["filename"] = filename
    result["categorize_mode"] = mode
    result["metadata"] = {
        k: v for k, v in meta.items()
        if k in ("filename", "domain", "sub_category", "keywords_json", "summary", "tags_json", "file_type", "estimated_tokens")
    }

    # C2.5 — surface mbox truncation on the ingest response so the UI can
    # warn the user that messages beyond the cap were silently dropped.
    if parsed.get("mbox_truncated"):
        result["mbox_truncated"] = True
        result["mbox_total_messages"] = parsed.get("mbox_total_messages")
        result["mbox_message_cap"] = parsed.get("mbox_message_cap")

    # RAG Cycle C2.4 — recursive email-attachment ingestion.
    # When the parsed file is an .eml / .mbox, the parser emits a list
    # of AttachmentBlob payloads under ``_attachments``. Each blob is
    # ingested as its own Artifact and linked back to the parent email
    # via a ``HAS_ATTACHMENT`` Neo4j edge. Failures are logged and
    # skipped — the parent ingest already succeeded, attachment recursion
    # is best-effort augmentation.
    attachments = parsed.get("_attachments") or []
    parent_artifact_id = result.get("artifact_id")
    if attachments and parent_artifact_id and result.get("status") in (
        "success", "updated", "duplicate"
    ):
        # Project the email's own header fields onto a shared dict so
        # each attachment artifact's metadata can carry the parent's
        # Message-ID / From / Subject for retrieval-side filtering.
        parent_meta_for_children: dict[str, Any] = dict(meta)
        if parsed.get("subject"):
            parent_meta_for_children["subject"] = parsed["subject"]
        if parsed.get("message_id"):
            parent_meta_for_children["message_id"] = parsed["message_id"]
        if parsed.get("from"):
            parent_meta_for_children["from"] = parsed["from"]
        attachment_summaries = await _ingest_email_attachments(
            attachments=attachments,
            parent_artifact_id=str(parent_artifact_id),
            parent_domain=domain,
            parent_meta=parent_meta_for_children,
        )
        if attachment_summaries:
            result["attachments_ingested"] = attachment_summaries

    return result


# ── Batch ingestion ──────────────────────────────────────────────────────────

# Concurrency limiter shared with single-file ingestion — prevents overloading
# ChromaDB / Neo4j with too many parallel writes.
_ingest_semaphore = asyncio.Semaphore(3)

BATCH_MAX_ITEMS = 20


async def ingest_batch(
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Ingest up to BATCH_MAX_ITEMS files/content entries concurrently.

    Each item should contain either ``file_path`` or ``content`` (not both).
    Returns per-item results with overall success/failure counts.
    Individual failures do not block the rest of the batch.
    """
    if len(items) > BATCH_MAX_ITEMS:
        raise ValueError(
            f"Batch size {len(items)} exceeds maximum ({BATCH_MAX_ITEMS})"
        )

    async def _ingest_one(item: dict[str, Any]) -> dict[str, Any]:
        """Ingest a single item under the shared semaphore."""
        async with _ingest_semaphore:
            try:
                if item.get("file_path"):
                    return await ingest_file(
                        file_path=item["file_path"],
                        domain=item.get("domain", ""),
                        sub_category=item.get("sub_category", ""),
                        tags=item.get("tags", ""),
                        categorize_mode=item.get("categorize_mode", ""),
                    )
                elif item.get("content"):
                    return await asyncio.to_thread(
                        ingest_content,
                        item["content"],
                        item.get("domain", "general"),
                        item.get("metadata"),
                    )
                else:
                    return {"status": "error", "error": "Item must have 'file_path' or 'content'"}
            except Exception as e:
                logger.error("Batch ingest item failed: %s", e)
                return {
                    "status": "error",
                    "error": str(e),
                    "file_path": item.get("file_path", ""),
                }

    results = await asyncio.gather(
        *[_ingest_one(item) for item in items],
        return_exceptions=True,
    )

    # Convert any bare exceptions to error dicts
    clean_results: list[dict[str, Any]] = []
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            clean_results.append({
                "status": "error",
                "error": str(r),
                "file_path": items[i].get("file_path", ""),
            })
        else:
            clean_results.append(r)  # type: ignore[arg-type]

    succeeded = sum(1 for r in clean_results if r.get("status") in ("success", "duplicate", "updated"))
    failed = len(clean_results) - succeeded

    return {
        "results": clean_results,
        "succeeded": succeeded,
        "failed": failed,
    }
