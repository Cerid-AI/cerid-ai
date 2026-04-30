# Copyright (c) 2026 Justin Michaels. All rights reserved.
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
from app.db import neo4j as graph
from app.deps import get_chroma, get_neo4j, get_redis
from app.parsers import parse_file
from core.context.identity import get_tenant_id
from core.utils import cache
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso
from utils.chunker import chunk_text, make_context_header
from utils.metadata import ai_categorize, extract_metadata, extract_metadata_minimal

logger = logging.getLogger("ai-companion")


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

    chunk_ids = [f"{artifact_id}_chunk_{i}" for i in range(len(chunks))]
    chunk_metadatas = [{**base_meta, "chunk_index": i} for i in range(len(chunks))]
    collection.add(ids=chunk_ids, documents=chunks, metadatas=chunk_metadatas)

    # BM25 index
    try:
        from core.retrieval.bm25 import index_chunks
        index_chunks(domain, chunk_ids, chunks)
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
            chunk_count=len(chunks),
            chunk_ids_json=json.dumps(chunk_ids),
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
    if pre_chunked:
        chunks = [c["text"] for c in pre_chunked]
        pre_chunk_metadatas = [
            dict(c.get("metadata", {})) for c in pre_chunked
        ]
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

    # Tag near-duplicate in metadata
    if near_dup:
        base_meta["near_duplicate_of"] = near_dup["artifact_id"]
        base_meta["near_duplicate_similarity"] = str(near_dup["similarity"])

    chunk_ids = [f"{artifact_id}_chunk_{i}" for i in range(len(chunks))]
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
            chunk_metadatas.append(merged)
    else:
        chunk_metadatas = [{**base_meta, "chunk_index": i} for i in range(len(chunks))]
    collection.add(ids=chunk_ids, documents=chunks, metadatas=chunk_metadatas)

    # Index for BM25 hybrid search
    try:
        from core.retrieval.bm25 import index_chunks
        index_chunks(domain, chunk_ids, chunks)
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
            chunk_count=len(chunks),
            chunk_ids_json=json.dumps(chunk_ids),
            content_hash=content_hash,
            sub_category=base_meta.get("sub_category", config.DEFAULT_SUB_CATEGORY),
            tags_json=base_meta.get("tags_json", "[]"),
            quality_score=quality_score,
            client_source=base_meta.get("client_source", ""),
        )
        artifact_created = True
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
        logger.error(f"Neo4j artifact creation failed: {e}")
        _rollback_chromadb(collection, chunk_ids)
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
) -> dict:
    """Parse a file, extract metadata, optionally AI-categorize, chunk, and store.

    ``skip_metadata`` swaps the NLP-heavy ``extract_metadata()`` (spaCy NER
    + tiktoken) for the fast ``extract_metadata_minimal()`` fallback used
    by the setup wizard — trades keyword/summary quality for sub-100ms
    latency. ``skip_quality`` is threaded into ``ingest_content`` and skips
    the 4-dimension quality scorer. Both flags are opt-in and default False;
    the frontend sets them on the wizard's "Try It Out" ingest so the user
    isn't waiting for metadata extraction before their first query.
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
