# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Core ingestion service functions.

Extracted from routers/ingestion.py to eliminate the circular import
between agents/memory.py → routers/ingestion.py. Routers and agents
both import from this service layer.
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
from db import neo4j as graph
from deps import get_chroma, get_neo4j, get_redis
from errors import CeridError, IngestionError
from parsers import parse_file
from utils import cache
from utils.chunker import PARENT_CHILD_ENABLED, chunk_text, make_context_header
from utils.metadata import ai_categorize, extract_metadata
from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion")


def _record_ingest_history(
    filename: str,
    source_type: str,
    domain: str,
    status: str,
    chunks: int = 0,
    error: str = "",
) -> None:
    """Push an ingestion event to the persistent Redis stream."""
    try:
        from routers.system_monitor import record_ingest_event

        record_ingest_event(
            filename=filename,
            source_type=source_type or "upload",
            domain=domain,
            status=status,
            chunks=chunks,
            error=error,
        )
    except (CeridError, ValueError, OSError, RuntimeError, ImportError, AttributeError, TypeError, KeyError) as e:
        logger.debug("Ingest history recording failed (non-blocking): %s", e)


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


def _rollback_chromadb(collection, chunk_ids: list[str]) -> None:
    """Compensating transaction: remove ChromaDB chunks when Neo4j write fails."""
    try:
        collection.delete(ids=chunk_ids)
        logger.warning(
            "Rolled back %d ChromaDB chunks after graph failure", len(chunk_ids),
        )
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(
            "CRITICAL: ChromaDB rollback failed for %d chunks — orphaned data: %s",
            len(chunk_ids), e,
        )


def _rollback_bm25(domain: str, chunk_ids: list[str]) -> None:
    """Compensating transaction: remove BM25 entries when Neo4j write fails."""
    try:
        from utils.bm25 import remove_chunks

        removed = remove_chunks(domain, chunk_ids)
        if removed:
            logger.warning(
                "Rolled back %d BM25 entries for domain '%s' after graph failure",
                removed,
                domain,
            )
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(
            "CRITICAL: BM25 rollback failed for %d chunks in '%s' — orphaned index entries: %s",
            len(chunk_ids),
            domain,
            e,
        )


def _push_to_dlq_sync(payload: dict, error: str, attempt: int = 1) -> None:
    """Synchronously push a failed ingestion to the DLQ (fire-and-forget)."""
    try:
        redis_client = get_redis()
        if redis_client is None:
            logger.error("DLQ push skipped — Redis unavailable")
            return
        from datetime import datetime, timedelta, timezone

        from utils.dlq import STREAM_KEY, _backoff_seconds

        now = datetime.now(timezone.utc)
        next_retry_at = now + timedelta(seconds=_backoff_seconds(attempt))
        entry = {
            "payload": json.dumps(payload),
            "error": str(error),
            "attempt": str(attempt),
            "next_retry_at": next_retry_at.isoformat(),
            "created_at": utcnow_iso(),
        }
        redis_client.xadd(STREAM_KEY, entry)
        logger.warning(
            "DLQ push (sync): attempt=%d error=%s", attempt, str(error)[:120]
        )
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as dlq_err:
        logger.error("Failed to push to DLQ: %s", dlq_err)


def _check_duplicate(content_hash: str, domain: str) -> dict | None:
    try:
        driver = get_neo4j()
        if driver is None:
            # Lightweight mode — fall back to ChromaDB metadata dedup
            try:
                chroma = get_chroma()
                coll_name = config.collection_name(domain)
                collection = chroma.get_or_create_collection(name=coll_name)
                results = collection.get(where={"content_hash": content_hash}, limit=1)
                if results and results.get("ids"):
                    meta = results["metadatas"][0] if results.get("metadatas") else {}
                    return {
                        "id": meta.get("artifact_id", results["ids"][0]),
                        "filename": meta.get("filename", "unknown"),
                        "domain": meta.get("domain", domain),
                    }
            except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
                logger.debug(f"ChromaDB dedup fallback failed: {e}")
            return None
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
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
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
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.warning(f"Failed to delete old chunks during re-ingest: {e}")

    # Create new chunks with contextual header
    filename = metadata.get("filename", "") if metadata else ""
    sub_cat = metadata.get("sub_category", "") if metadata else ""
    ctx_header = make_context_header(filename=filename, domain=domain, sub_category=sub_cat)
    # Parent-child chunking (feature-flagged via ENABLE_PARENT_CHILD_RETRIEVAL)
    _pc_chunks_reingest: list[dict] | None = None
    try:
        if PARENT_CHILD_ENABLED:
            from utils.chunker import chunk_with_parents
            _pc_chunks_reingest = chunk_with_parents(
                content, artifact_id=artifact_id,
                max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP,
                context_header=ctx_header,
            )
            chunks = [c["text"] for c in _pc_chunks_reingest]
        else:
            chunks = chunk_text(
                content, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP,
                context_header=ctx_header,
            )
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning("Parent-child chunking failed (re-ingest), falling back: %s", e)
        _pc_chunks_reingest = None
        chunks = chunk_text(
            content, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP,
            context_header=ctx_header,
        )

    # Contextual enrichment — LLM-generated situational summaries per chunk
    if config.ENABLE_CONTEXTUAL_CHUNKS:
        try:
            from utils.contextual import contextualize_chunks
            chunks = contextualize_chunks(chunks, content, metadata)
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.warning("Contextual enrichment skipped (re-ingest): %s", e)

    base_meta = {"domain": domain, "artifact_id": artifact_id, "ingested_at": utcnow_iso()}
    if metadata:
        base_meta.update(metadata)

    if _pc_chunks_reingest is not None:
        chunk_ids = [c["chunk_id"] for c in _pc_chunks_reingest]
        chunk_metadatas = [
            {**base_meta, "chunk_index": i,
             "chunk_level": pc["chunk_level"],
             "parent_chunk_id": pc.get("parent_chunk_id") or ""}
            for i, pc in enumerate(_pc_chunks_reingest)
        ]
    else:
        chunk_ids = [f"{artifact_id}_chunk_{i}" for i in range(len(chunks))]
        chunk_metadatas = [{**base_meta, "chunk_index": i} for i in range(len(chunks))]
    collection.add(ids=chunk_ids, documents=chunks, metadatas=chunk_metadatas)

    # BM25 index
    try:
        from utils.bm25 import index_chunks
        index_chunks(domain, chunk_ids, chunks)
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.debug(f"BM25 indexing failed during re-ingest (non-blocking): {e}")

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
        driver = get_neo4j()
        if driver is not None:
            graph.update_artifact(
                driver,
                artifact_id=artifact_id,
                keywords_json=base_meta.get("keywords_json", "[]"),
                summary=base_meta.get("summary", content[:200]),
                chunk_count=len(chunks),
                chunk_ids_json=json.dumps(chunk_ids),
                content_hash=content_hash,
                quality_score=quality_score,
            )
        else:
            logger.debug("Lightweight mode — skipping Neo4j artifact update for re-ingest")
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
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
    triage_result: Any | None = None,
    skip_quality: bool = False,
    skip_metadata: bool = False,
) -> dict:
    """Core ingest path. Called by REST endpoints, agents, and MCP tool dispatcher.

    Args:
        triage_result: Optional ``TriageResult`` from ``agents.triage``.
            When provided, gates ingestion (``should_ingest``), seeds
            ``quality_score``, applies ``recommended_domain`` as fallback,
            and merges ``suggested_tags``.
    """
    # --- Triage gate ---
    if triage_result is not None:
        if not getattr(triage_result, "should_ingest", True):
            reason = getattr(triage_result, "skip_reason", None) or "Triage rejected"
            logger.info(
                "Skipping ingestion (triage gate): %s — %s",
                (metadata or {}).get("filename", "?"), reason,
            )
            _record_ingest_history(
                filename=(metadata or {}).get("filename", "?"),
                source_type=(metadata or {}).get("client_source", "upload"),
                domain=domain, status="skipped",
            )
            return {
                "status": "skipped",
                "reason": reason,
                "domain": domain,
                "chunks": 0,
                "timestamp": utcnow_iso(),
            }

        # Use triage domain as fallback when caller didn't specify
        rec_domain = getattr(triage_result, "recommended_domain", "")
        if rec_domain and (not domain or domain == "general"):
            domain = rec_domain

        # Seed suggested tags into metadata
        suggested_tags = getattr(triage_result, "suggested_tags", [])
        if suggested_tags:
            metadata = dict(metadata) if metadata else {}
            existing_tags: list[str] = []
            if metadata.get("tags_json"):
                try:
                    existing_tags = json.loads(metadata["tags_json"])
                except (json.JSONDecodeError, TypeError):
                    pass
            merged_tags = list(dict.fromkeys(existing_tags + suggested_tags))
            metadata["tags_json"] = json.dumps(merged_tags)
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
        _record_ingest_history(
            filename=fname,
            source_type=(metadata or {}).get("client_source", "upload"),
            domain=domain, status="skipped",
        )
        return {
            "status": "duplicate",
            "artifact_id": existing["id"],
            "domain": existing["domain"],
            "chunks": 0,
            "timestamp": utcnow_iso(),
            "duplicate_of": existing["filename"],
        }

    # Semantic deduplication (community feature — KB quality for all tiers)
    near_dup = None
    try:
        from utils.dedup import check_semantic_duplicate

        near_dup = check_semantic_duplicate(
            text=content,
            domain=domain,
            chroma_client=get_chroma(),
        )
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.debug(f"Semantic dedup check skipped: {e}")

    # Re-ingestion check: same filename, different content
    fname = (metadata or {}).get("filename", "text_input")
    if fname != "text_input":
        try:
            _neo4j_driver = get_neo4j()
            if _neo4j_driver is not None:
                prev = graph.find_artifact_by_filename(_neo4j_driver, fname, domain)
                if prev and prev["content_hash"] != content_hash:
                    return _reingest_artifact(prev, content, domain, metadata, content_hash)
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.warning(f"Re-ingest check failed (proceeding as new): {e}")

    fname_for_header = (metadata or {}).get("filename", "")
    sub_cat_for_header = (metadata or {}).get("sub_category", "")
    ctx_header = make_context_header(
        filename=fname_for_header, domain=domain, sub_category=sub_cat_for_header,
    )
    # Parent-child chunking (feature-flagged via ENABLE_PARENT_CHILD_RETRIEVAL)
    _pc_chunks: list[dict] | None = None
    try:
        if PARENT_CHILD_ENABLED:
            from utils.chunker import chunk_with_parents
            _pc_chunks = chunk_with_parents(
                content, artifact_id=artifact_id,
                max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP,
                context_header=ctx_header,
            )
            chunks = [c["text"] for c in _pc_chunks]
        else:
            chunks = chunk_text(
                content, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP,
                context_header=ctx_header,
            )
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning("Parent-child chunking failed, falling back to standard: %s", e)
        _pc_chunks = None
        chunks = chunk_text(
            content, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP,
            context_header=ctx_header,
        )

    # Contextual enrichment — LLM-generated situational summaries per chunk
    if config.ENABLE_CONTEXTUAL_CHUNKS:
        try:
            from utils.contextual import contextualize_chunks
            chunks = contextualize_chunks(chunks, content, metadata)
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.warning("Contextual enrichment skipped: %s", e)

    ingested_at = utcnow_iso()
    base_meta = {"domain": domain, "artifact_id": artifact_id, "ingested_at": ingested_at}
    if metadata:
        base_meta.update(metadata)

    # When skip_metadata is set (wizard fast-path), generate lightweight
    # summary and keywords from the content itself instead of calling the LLM.
    if skip_metadata and not base_meta.get("summary"):
        base_meta["summary"] = content[:200].strip()
        fname = base_meta.get("filename", "")
        base_meta.setdefault(
            "keywords_json",
            json.dumps([w for w in Path(fname).stem.replace("_", " ").replace("-", " ").split() if w][:5])
            if fname else "[]",
        )

    # Propagate client_source for provenance tracking
    if metadata and metadata.get("client_source"):
        base_meta["client_source"] = metadata["client_source"]

    # Tag near-duplicate in metadata
    if near_dup:
        base_meta["near_duplicate_of"] = near_dup["artifact_id"]
        base_meta["near_duplicate_similarity"] = str(near_dup["similarity"])

    # Build chunk IDs and metadata — parent-child aware when enabled
    if _pc_chunks is not None:
        chunk_ids = [c["chunk_id"] for c in _pc_chunks]
    else:
        chunk_ids = [f"{artifact_id}_chunk_{i}" for i in range(len(chunks))]

    # Compute per-chunk retrieval profile for scoring strategy selection
    from utils.retrieval_profile import compute_retrieval_profile, serialize_profile

    _file_type = base_meta.get("file_type", metadata.get("file_type", "") if metadata else "")
    _page_count = metadata.get("page_count") if metadata else None
    _table_count = metadata.get("table_count") if metadata else None
    _artifact_profile = compute_retrieval_profile(
        content, file_type=_file_type, page_count=_page_count, table_count=_table_count,
    )
    _profile_json = serialize_profile(_artifact_profile)

    if _pc_chunks is not None:
        # Store parent-child hierarchy metadata in ChromaDB for retrieval-time lookup
        chunk_metadatas = []
        for i, pc in enumerate(_pc_chunks):
            meta = {
                **base_meta,
                "chunk_index": i,
                "retrieval_profile": _profile_json,
                "chunk_level": pc["chunk_level"],
                "parent_chunk_id": pc.get("parent_chunk_id") or "",
            }
            chunk_metadatas.append(meta)
    else:
        chunk_metadatas = [
            {**base_meta, "chunk_index": i, "retrieval_profile": _profile_json}
            for i in range(len(chunks))
        ]
    # Batch ChromaDB writes for large documents (>5000 chunks)
    from config.constants import CHROMA_MAX_BATCH_SIZE

    if len(chunks) <= CHROMA_MAX_BATCH_SIZE:
        collection.add(ids=chunk_ids, documents=chunks, metadatas=chunk_metadatas)
    else:
        for start in range(0, len(chunks), CHROMA_MAX_BATCH_SIZE):
            end = min(start + CHROMA_MAX_BATCH_SIZE, len(chunks))
            collection.add(
                ids=chunk_ids[start:end],
                documents=chunks[start:end],
                metadatas=chunk_metadatas[start:end],
            )

    # Index for BM25 hybrid search
    try:
        from utils.bm25 import index_chunks
        index_chunks(domain, chunk_ids, chunks)
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning(f"BM25 indexing failed (non-blocking): {e}")

    # Read-back first chunk to confirm ChromaDB write flushed
    try:
        collection.get(ids=[chunk_ids[0]])
    except (ValueError, KeyError, IndexError):
        pass  # Non-critical — query retry handles eventual consistency

    # Compute quality_score using weighted 4-dimension formula
    if skip_quality:
        quality_score = 0.5
    else:
        from utils.quality import compute_quality_score as _compute_quality

        quality_score = _compute_quality(
            summary=base_meta.get("summary", ""),
            keywords=base_meta.get("keywords_json", "[]"),
            tags=base_meta.get("tags_json", "[]"),
            sub_category=base_meta.get("sub_category", ""),
            default_sub_category=config.DEFAULT_SUB_CATEGORY,
            ingested_at=base_meta.get("ingested_at"),
            content=content,
            domain=domain,
            source_type=base_meta.get("client_source", "upload"),
        )

    # If triage provided an AI-derived quality_score, use the higher of the two
    if triage_result is not None:
        triage_qs = getattr(triage_result, "quality_score", 0.0)
        if triage_qs > quality_score:
            quality_score = round(triage_qs, 2)

    artifact_created = False
    driver = get_neo4j()
    if driver is not None:
        try:
            graph.create_artifact(
                driver,
                artifact_id=artifact_id,
                filename=base_meta.get("filename", "text_input"),
                domain=domain,
                keywords_json=base_meta.get("keywords_json", "[]"),
                summary=base_meta.get("summary", content[:200]),
                chunk_count=len(chunks),
                chunk_ids_json=json.dumps(chunk_ids),
                content_hash=content_hash,
                sub_category=base_meta.get("sub_category", config.DEFAULT_SUB_CATEGORY),
                tags_json=base_meta.get("tags_json", "[]"),
                quality_score=quality_score,
                client_source=base_meta.get("client_source", ""),
            )
            artifact_created = True
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            err_msg = str(e).lower()
            if "constraint" in err_msg and "content_hash" in err_msg:
                logger.info(f"Concurrent duplicate detected via constraint: {base_meta.get('filename', '?')}")
                _rollback_chromadb(collection, chunk_ids)
                _rollback_bm25(domain, chunk_ids)
                return {
                    "status": "duplicate",
                    "artifact_id": artifact_id,
                    "domain": domain,
                    "chunks": 0,
                    "timestamp": utcnow_iso(),
                    "duplicate_of": "(concurrent)",
                }
            logger.error(f"Neo4j artifact creation failed: {e}")
            _rollback_chromadb(collection, chunk_ids)
            _rollback_bm25(domain, chunk_ids)
            # Push to DLQ if not already a DLQ retry
            dlq_attempt = (metadata or {}).get("_dlq_attempt")
            if not dlq_attempt:
                _push_to_dlq_sync(
                    {"content": content[:5000], "domain": domain, "metadata": metadata},
                    error=str(e),
                )
            _record_ingest_history(
                filename=base_meta.get("filename", "text_input"),
                source_type=base_meta.get("client_source", "upload"),
                domain=domain, status="failed", error=str(e),
            )
            return {
                "status": "error",
                "artifact_id": artifact_id,
                "domain": domain,
                "chunks": 0,
                "timestamp": utcnow_iso(),
                "error": f"Graph storage failed: {e}",
            }
    else:
        # Lightweight mode — store content_hash in ChromaDB metadata for dedup
        logger.debug("Lightweight mode — skipping Neo4j artifact creation")
        # Add content_hash to existing chunk metadatas for dedup fallback
        try:
            collection.update(
                ids=chunk_ids[:1],
                metadatas=[{**chunk_metadatas[0], "content_hash": content_hash}],
            )
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.debug(f"Content hash metadata update failed: {e}")

    try:
        cache.log_event(
            get_redis(),
            event_type="ingest",
            artifact_id=artifact_id,
            domain=domain,
            filename=base_meta.get("filename", "text_input"),
        )
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Redis log failed: {e}")

    # Discover and create relationships with existing artifacts
    relationships_created = 0
    if artifact_created and driver is not None:
        try:
            relationships_created = graph.discover_relationships(
                driver=driver,
                artifact_id=artifact_id,
                filename=base_meta.get("filename", "text_input"),
                domain=domain,
                keywords_json=base_meta.get("keywords_json", "[]"),
                content=content[:5000],  # limit content scan for performance
            )
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.warning(f"Relationship discovery failed (non-blocking): {e}")

    # Fire webhook notification
    try:
        from utils.webhooks import notify_ingestion_complete
        asyncio.get_running_loop().create_task(
            notify_ingestion_complete(artifact_id, domain, base_meta.get("filename", "text_input"), len(chunks))
        )
    except RuntimeError:
        pass  # no running loop (e.g. sync context) — webhook skipped
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.debug(f"Webhook notification failed (non-blocking): {e}")

    # Surface related artifacts in response
    related = []
    if relationships_created > 0 and driver is not None:
        try:
            found = graph.find_related_artifacts(
                driver, artifact_ids=[artifact_id], depth=1, max_results=5,
            )
            related = [
                {"id": r["id"], "filename": r["filename"], "domain": r["domain"],
                 "relationship_type": r.get("relationship_type", "")}
                for r in found
            ]
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.debug(f"Related artifacts lookup failed (non-blocking): {e}")

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

    # Record to ingestion history stream
    _record_ingest_history(
        filename=base_meta.get("filename", "text_input"),
        source_type=base_meta.get("client_source", "upload"),
        domain=domain,
        status="success",
        chunks=len(chunks),
    )

    return result


async def ingest_file(
    file_path: str,
    domain: str = "",
    sub_category: str = "",
    tags: str = "",
    categorize_mode: str = "",
    client_source: str = "",
    triage_result: Any | None = None,
) -> dict:
    """Parse a file, extract metadata, optionally AI-categorize, chunk, and store."""
    validate_file_path(file_path)
    filename = Path(file_path).name
    # Run sync parser in thread pool to avoid blocking the event loop
    # (CPU-bound: PDF/DOCX parsing can take 100ms–2s per file)
    parsed = await asyncio.to_thread(parse_file, file_path)
    text = parsed["text"]
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
    if parsed.get("table_count") is not None:
        meta["table_count"] = parsed["table_count"]
    if client_source:
        meta["client_source"] = client_source
    # Run sync ingest_content in thread pool to avoid blocking the event loop
    # (I/O-bound: Neo4j, ChromaDB, Redis writes + CPU-bound tiktoken chunking)
    result = await asyncio.to_thread(ingest_content, text, domain, meta, triage_result)
    result["filename"] = filename
    result["categorize_mode"] = mode
    result["metadata"] = {
        k: v for k, v in meta.items()
        if k in ("filename", "domain", "sub_category", "keywords_json", "summary", "tags_json", "file_type", "estimated_tokens")
    }
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
            except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
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
