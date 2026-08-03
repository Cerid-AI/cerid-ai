# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Concrete BaseJob subclass for wiki entity page refresh (Phase W.1 + API.3).

Generates or updates the 2–3 paragraph prose summary on a single entity
node by:
  1. Fetching the entity's source artifacts + chunks from Neo4j/ChromaDB.
  2. Calling the local LLM helper (same Ollama path as EntityExtractionJob)
     with a summarisation prompt.
  3. Writing the result back to the entity node via the wiki Neo4j adapter.
  4. (API.3) Optionally enriching the entity page with external-API references
     via :func:`app.services.external_apis.wiki_enrichment.enrich`.

Payload schema
--------------
  {"entity_slug": str}

The job is enqueued by the background processor (WikiRefreshJob.job_type =
"wiki_refresh") whenever an entity's evidence shifts — specifically when
its edge count grows past a threshold or its summary embedding drifts > τ.
Both trigger points live in the processor's event hooks (Phase P.5).

Feature flag
------------
``WIKI_ENRICHMENT_ENABLED`` (env var, default ``"true"``).  Set to ``"false"``
or ``"0"`` to skip the external enrichment step entirely.  When disabled the
entity page will have an empty ``external_references`` list.
"""
from __future__ import annotations

import asyncio
import logging
import os
from decimal import Decimal
from typing import Any

from core.agents.entity_extraction import is_junk_entity_name
from core.agents.summary_quality import (
    INSUFFICIENT_SENTINEL,
    is_insufficient_summary,
)
from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.processor.wiki_refresh")


def _enrichment_enabled() -> bool:
    """Return True when WIKI_ENRICHMENT_ENABLED is not explicitly disabled."""
    val = os.environ.get("WIKI_ENRICHMENT_ENABLED", "true").strip().lower()
    return val not in ("false", "0", "no", "off")

# Typical token budget: concatenated entity chunks (moderate doc) ~3000 in,
# 800 out for a 2–3 paragraph summary on the local Ollama path.
_EST_TOKENS_IN = 3_000
_EST_TOKENS_OUT = 800
_MODEL = "ollama/local"
_MAX_CHARS = 12_000  # concat budget for entity text corpus

_SUMMARY_PROMPT = """\
You are summarising a named entity based on excerpts from a knowledge corpus.
Write 2–3 concise paragraphs describing what is known about this entity, \
its significance, and key relationships. Use only information from the excerpts \
provided. Do not invent facts. Be direct and encyclopedic.

If the excerpts contain no substantive information about this entity itself — \
only passing mentions, or content about something else — reply with exactly \
{sentinel} and nothing else. Do not write a summary explaining that the \
entity is absent, and do not summarise the other material instead.

Entity: {entity_name} ({entity_type})

Excerpts:
\"\"\"
{text}
\"\"\"

Summary (2–3 paragraphs):
"""


class WikiRefreshJob(BaseJob):
    """Generate or refresh the prose summary for a single entity.

    Parameters
    ----------
    entity_slug
        The canonical_id of the entity node to summarise
        (e.g. ``"person:elon-musk"``).
    """

    job_type = "wiki_refresh"

    def __init__(self, entity_slug: str) -> None:
        self._entity_slug = entity_slug

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        """Zero USD cost — wiki refresh runs on the local Ollama path."""
        return CostEstimate(
            estimated_tokens_in=_EST_TOKENS_IN,
            estimated_tokens_out=_EST_TOKENS_OUT,
            model=_MODEL,
            estimated_usd=Decimal("0.00"),
            confidence="medium",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        """Execute the wiki refresh pipeline.

        Progress checkpoints
        --------------------
        0.0  — job started
        0.3  — entity data + source text fetched from Neo4j / ChromaDB
        0.6  — LLM summary generated
        0.9  — summary written back to Neo4j
        0.95 — external enrichment complete (API.3; skipped when disabled)
        1.0  — done

        Propagates exceptions after logging so the worker can record
        failure and schedule a retry.
        """
        await progress_cb(0.0)
        logger.info("wiki_refresh.start entity=%s", self._entity_slug)

        try:
            stats = await self._run_pipeline(progress_cb)
        except Exception as exc:
            log_swallowed_error(
                "processor.wiki_refresh",
                exc,
                context={"entity_slug": self._entity_slug},
            )
            raise

        return JobResult(
            job_id="",  # filled in by the worker after dequeue
            actual_tokens_in=stats.get("tokens_in", _EST_TOKENS_IN),
            actual_tokens_out=stats.get("tokens_out", _EST_TOKENS_OUT),
            metadata={
                "entity_slug": self._entity_slug,
                "summary_chars": stats.get("summary_chars", 0),
                "artifacts_used": stats.get("artifacts_used", 0),
                "skipped": stats.get("skipped"),
                "external_refs_count": stats.get("external_refs_count", 0),
            },
        )

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    async def _run_pipeline(self, progress_cb: ProgressCallback) -> dict[str, Any]:
        """Fetch entity data, generate summary, write back."""
        from app.db.neo4j.wiki import get_entity, write_entity_summary
        from app.deps import get_chroma, get_neo4j

        driver = get_neo4j()
        chroma_client = get_chroma()

        # --- 0.3: Fetch entity + source text ---------------------------------
        entity_raw = await asyncio.to_thread(get_entity, driver, self._entity_slug)
        if entity_raw is None:
            logger.warning("wiki_refresh.entity_not_found slug=%s", self._entity_slug)
            return {"skipped": "entity_not_found"}

        entity_name = entity_raw.get("name", self._entity_slug)
        entity_type = entity_raw.get("entity_type", "OTHER")
        source_artifacts = entity_raw.get("source_artifacts", [])

        # Junk-name gate — the single choke point covering every producer
        # (ingest hook, nightly stale sweep, manual enqueue). Fires before
        # the LLM summary, so a re-enqueued junk entity costs one Neo4j
        # read instead of 40-110s of LLM + external-API time.
        if is_junk_entity_name(str(entity_name)):
            logger.info(
                "wiki_refresh.skip_junk_entity slug=%s name=%r",
                self._entity_slug,
                entity_name,
            )
            return {"skipped": "junk_entity_name"}

        if not source_artifacts:
            return {"skipped": "no_source_artifacts"}

        # Collect chunks from ChromaDB for the entity's artifact set
        artifact_ids = [s["artifact_id"] for s in source_artifacts if s.get("artifact_id")]
        chunk_texts = await asyncio.to_thread(
            self._fetch_entity_chunks, chroma_client, artifact_ids, self._entity_slug
        )

        await progress_cb(0.3)

        if not chunk_texts:
            return {"skipped": "no_chunks", "artifacts_used": 0}

        blob = "\n\n---\n\n".join(chunk_texts)
        if len(blob) > _MAX_CHARS:
            blob = blob[:_MAX_CHARS]

        # --- 0.6: LLM summary ------------------------------------------------
        # Prose summariser — NOT entity_extraction's default_llm_caller, which
        # forces response_format=json_object. Wiki summaries are prose; json
        # mode 400s on the OpenRouter fallback (gpt-4o-mini requires "json" in
        # the prompt for json_object), so this path must request free text.
        from core.utils.internal_llm import call_internal_llm

        prompt_messages = [
            {
                "role": "system",
                "content": (
                    "You are a precise knowledge summariser. Write in encyclopedic "
                    "prose. Never add information not present in the excerpts."
                ),
            },
            {
                "role": "user",
                "content": _SUMMARY_PROMPT.format(
                    entity_name=entity_name,
                    entity_type=entity_type,
                    text=blob,
                    sentinel=INSUFFICIENT_SENTINEL,
                ),
            },
        ]

        try:
            summary_text = await call_internal_llm(
                prompt_messages,
                temperature=0.2,
                max_tokens=1024,
                stage="wiki_summary",
            )
        except Exception as exc:
            # A genuine LLM fault is fatal: re-raise so the worker records the
            # job as FAILED. Returning a "skipped" dict would be marked
            # COMPLETED and evade failure-keyed alerting (AF-038).
            logger.error(
                "wiki_refresh.llm_call_failed entity=%s: %s",
                self._entity_slug,
                exc,
            )
            raise

        summary_text = (summary_text or "").strip()
        if not summary_text:
            return {"skipped": "empty_summary"}

        # Refuse to store a summary that denies its own subject. Left in, these
        # do active harm rather than merely being useless: the page is served as
        # high-priority grounding on the answer path, so the reader is handed a
        # paragraph saying the entity is absent, followed by content about
        # something else. Both checks run because a local 8B model follows the
        # sentinel instruction only most of the time.
        #
        # An existing summary is deliberately left in place. One thin refresh
        # (a re-summarise triggered before new artifacts land) should not
        # delete a good page — the entity keeps its previous text and the next
        # refresh with better excerpts replaces it.
        if is_insufficient_summary(summary_text):
            logger.info(
                "wiki_refresh.insufficient_excerpts entity=%s chars=%d",
                self._entity_slug,
                len(summary_text),
            )
            return {"skipped": "insufficient_excerpts"}

        await progress_cb(0.6)

        # --- 0.9: Write summary to Neo4j -------------------------------------
        now_iso = utcnow_iso()
        await asyncio.to_thread(
            write_entity_summary, driver, self._entity_slug, summary_text, now_iso
        )

        await progress_cb(0.9)

        # --- 0.95: External enrichment (API.3) -------------------------------
        external_refs_count = 0
        if _enrichment_enabled():
            try:
                from app.services.external_apis import registry as _registry  # noqa: PLC0415
                from app.services.external_apis import wiki_enrichment  # noqa: PLC0415

                entity_type = wiki_enrichment.infer_entity_type(
                    entity_name,
                    related_entities=[
                        r.get("name", "") for r in entity_raw.get("related", [])
                    ],
                )
                refs = await wiki_enrichment.enrich(
                    entity_name=entity_name,
                    entity_type=entity_type,
                    registry=_registry,
                )
                if refs:
                    from app.db.neo4j.wiki import write_external_references  # noqa: PLC0415

                    ref_dicts = [r.model_dump() for r in refs]
                    await asyncio.to_thread(
                        write_external_references, driver, self._entity_slug, ref_dicts
                    )
                    external_refs_count = len(refs)
                    logger.info(
                        "wiki_refresh.enriched entity=%s refs=%d type=%s",
                        self._entity_slug,
                        external_refs_count,
                        entity_type,
                    )
            except Exception as exc:  # noqa: BLE001
                log_swallowed_error(
                    "processor.wiki_refresh.enrichment",
                    exc,
                    context={"entity_slug": self._entity_slug},
                )
                # Non-fatal: enrichment failure does not abort the job

        await progress_cb(0.95)

        # Phase K4.1 — append to the knowledge log so the user (and
        # the LLM via /wiki/log) can see what changed and when.
        # Karpathy's log.md equivalent — chronological breadcrumb of
        # everything the system learned.
        try:
            from app.db.neo4j.knowledge_log import append_log_entry  # noqa: PLC0415

            action_kind = "enrich" if external_refs_count > 0 else "refresh"
            log_summary = summary_text[:200].replace("\n", " ")
            await asyncio.to_thread(
                append_log_entry,
                driver,
                action=action_kind,
                entity_slug=self._entity_slug,
                summary=log_summary,
                source_artifact_id=None,
            )
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "processor.wiki_refresh.knowledge_log",
                exc,
                context={"entity_slug": self._entity_slug},
            )

        await progress_cb(1.0)

        logger.info(
            "wiki_refresh.done entity=%s chars=%d artifacts=%d ext_refs=%d",
            self._entity_slug,
            len(summary_text),
            len(artifact_ids),
            external_refs_count,
        )
        return {
            "summary_chars": len(summary_text),
            "artifacts_used": len(artifact_ids),
            "tokens_in": _EST_TOKENS_IN,
            "tokens_out": _EST_TOKENS_OUT,
            "external_refs_count": external_refs_count,
        }

    @staticmethod
    def _fetch_entity_chunks(
        chroma_client: Any,
        artifact_ids: list[str],
        entity_slug: str,
        max_chunks: int = 40,
    ) -> list[str]:
        """Synchronous ChromaDB query — run in a thread.

        Queries each artifact's collection for chunks that mention the entity
        slug. Falls back to fetching the most recent chunks from any domain
        collection when entity-scoped chunks are not found.
        """
        from config.taxonomy import DOMAINS, collection_name  # type: ignore[import]

        seen_ids: set[str] = set()
        texts: list[str] = []

        for domain in DOMAINS:
            if len(texts) >= max_chunks:
                break
            try:
                coll = chroma_client.get_collection(name=collection_name(domain))
            except Exception:  # noqa: BLE001 — missing collection is a valid skip
                continue

            # Attempt to fetch chunks that belong to the entity's artifacts
            remaining = max_chunks - len(texts)
            if not artifact_ids:
                continue
            try:
                res = coll.get(
                    where={"artifact_id": {"$in": artifact_ids[:10]}},
                    include=["documents"],
                    limit=remaining,
                )
                for doc_id, doc in zip(
                    res.get("ids", []), res.get("documents", []) or []
                ):
                    if doc_id not in seen_ids and doc:
                        seen_ids.add(doc_id)
                        texts.append(doc)
            except Exception as exc:  # noqa: BLE001 — collection-level error is skippable
                log_swallowed_error(
                    "app.processor.jobs.wiki_refresh.fetch_chunks", exc
                )
                continue

        return texts
