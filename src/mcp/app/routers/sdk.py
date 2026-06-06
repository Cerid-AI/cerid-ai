# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stable SDK endpoints for external cerid-series consumers.

These thin facades delegate to the existing agent/health endpoints,
providing a versioned contract (``/sdk/v1/``) that survives internal
refactoring of the ``/agent/`` paths.

Consumers should send ``X-Client-ID`` for per-client rate limiting and
domain access control.  See ``config.settings.CONSUMER_REGISTRY`` for
the per-consumer configuration and ``docs/INTEGRATION_GUIDE.md`` for
adding new cerid-series consumers.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

import config
from app.middleware.idempotency import idempotent
from app.models.sdk import (
    SDKHallucinationResponse,
    SDKHealthResponse,
    SDKLLMCompleteRequest,
    SDKLLMCompleteResponse,
    SDKMemoryExtractAcceptedResponse,
    SDKMemoryExtractJobStatus,
    SDKMemoryExtractResponse,
    SDKQueryResponse,
)
from app.routers.agents import (
    AgentQueryRequest,
    HallucinationCheckRequest,
    MemoryExtractionRequest,
    agent_query_endpoint,
    hallucination_check_endpoint,
    memory_extract_endpoint,
)
from app.routers.health import degradation_status, health_check, list_collections
from app.routers.plugins import list_plugins
from app.routers.query import query_knowledge
from app.routers.sdk_version import SDK_VERSION
from app.services.external_ingest import ExternalIngestRequest, IngestResult, ingest_external
from app.services.ingestion import ingest_content, ingest_file
from config.features import FEATURE_FLAGS, FEATURE_TIER
from config.taxonomy import DOMAINS, TAXONOMY

router = APIRouter(prefix="/sdk/v1", tags=["SDK"])

_503 = {"description": "One or more backend services unavailable"}
_422 = {"description": "Invalid request parameters"}


# ---------------------------------------------------------------------------
# Queue-availability probes (defensive imports for the public distribution)
# ---------------------------------------------------------------------------
#
# ``app.queue`` is internal-only per .sync-manifest.yaml — community/public
# installs ship without it. The async memory_extract path is internal-only
# by design ("community installs without queue workers don't pay the cost"
# — app/queue/__init__.py docstring); the sync path is the universal
# contract. Detect the gap once at module import time and short-circuit
# the async branches accordingly.

try:
    from app.queue import (  # noqa: F401  — re-exported via _queue_avail
        _rq_available,
        get_memory_queue,
        is_memory_async_mode,
    )
    _queue_avail = True
except ImportError:
    _queue_avail = False
    _rq_available = False  # type: ignore[assignment]

    def is_memory_async_mode() -> bool:  # type: ignore[no-redef]
        """Public-distribution stub — async queue isn't shipped here."""
        return False

    def get_memory_queue():  # type: ignore[no-redef]
        raise RuntimeError("Async memory queue is internal-only.")


# ---------------------------------------------------------------------------
# Core endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/query",
    response_model=SDKQueryResponse,
    summary="KB Query",
    description="Multi-domain knowledge base search with hybrid BM25+vector retrieval and optional LLM reranking. "
    "Results are scoped by the consumer's allowed_domains in CONSUMER_REGISTRY.",
    responses={422: _422, 503: _503},
)
async def sdk_query(req: AgentQueryRequest, request: Request):
    return await agent_query_endpoint(req, request)


@router.post(
    "/hallucination",
    response_model=SDKHallucinationResponse,
    summary="Hallucination Detection",
    description="Verify factual claims in a response against the KB. Returns per-claim status "
    "(verified/unverified/uncertain) with sources and confidence scores.",
    responses={422: _422, 503: _503},
)
async def sdk_hallucination(req: HallucinationCheckRequest):
    return await hallucination_check_endpoint(req)


@router.post(
    "/memory/extract",
    summary="Memory Extraction",
    description=(
        "Extract facts, decisions, and preferences from conversation text "
        "and store as KB artifacts. Deduplicates against existing memories.\n\n"
        "When ``MEMORY_QUEUE_MODE=async`` is set on the server (and a worker "
        "is running), the default behaviour is **fire-and-forget**: returns "
        "``202 Accepted`` immediately with a ``job_id`` and a ``status_url``. "
        "Poll ``GET /sdk/v1/memory/extract/jobs/{job_id}`` for the result.\n\n"
        "Pass ``?wait=true`` to force the synchronous path (waits for the "
        "full extract → consolidate → store pipeline before responding) — "
        "use for callers that need the result envelope inline."
    ),
    responses={
        200: {"model": SDKMemoryExtractResponse, "description": "Sync result (wait=true or queue disabled)"},
        202: {"model": SDKMemoryExtractAcceptedResponse, "description": "Accepted for async processing"},
        422: _422,
        503: _503,
    },
)
async def sdk_memory_extract(req: MemoryExtractionRequest, request: Request, wait: bool = False):
    """Default-async endpoint with sync escape hatch.

    Routing:
      * ``wait=true`` OR ``MEMORY_QUEUE_MODE != async``  → sync path
        (existing behaviour: full extract→consolidate→store, returns
        ``SDKMemoryExtractResponse`` 200).
      * Otherwise  → enqueue + 202 ``SDKMemoryExtractAcceptedResponse``.

    The sync escape hatch keeps every existing caller binary-compatible
    until they choose to switch. New callers should default to async and
    poll the status endpoint — that's what the systemic class-of-problem
    answer asks for: post-fact annotation work doesn't belong on the
    request critical path.
    """
    from fastapi.responses import JSONResponse

    if wait or not is_memory_async_mode():
        # Idempotency-Key (GA P0.5 D1) on the sync path only; the async/202 path
        # is already job-id idempotent via the queue.
        return await idempotent(request, lambda: memory_extract_endpoint(req))

    queue = get_memory_queue()
    job = queue.enqueue(
        "app.queue.tasks.memory_extract_task",
        kwargs={
            "response_text": req.response_text,
            "conversation_id": req.conversation_id,
            "model": req.model,
        },
        # Keep results around long enough for trading-agent's reflection
        # loop (60s scan interval × ~10 cycles before a stale poll = 10 min
        # ceiling). Failed jobs persist for the same window so operators
        # can inspect the traceback via ``rq info``.
        result_ttl=600,
        failure_ttl=600,
    )
    accepted = SDKMemoryExtractAcceptedResponse(
        job_id=job.id,
        status="queued",
        status_url=f"/sdk/v1/memory/extract/jobs/{job.id}",
        conversation_id=req.conversation_id,
    )
    return JSONResponse(
        status_code=202,
        content=accepted.model_dump(),
        headers={"Location": accepted.status_url},
    )


@router.get(
    "/memory/extract/jobs/{job_id}",
    response_model=SDKMemoryExtractJobStatus,
    summary="Memory Extract Job Status",
    description=(
        "Poll for the result of an async memory_extract job. Status "
        "transitions: ``queued → started → finished`` (success) or "
        "``queued → started → failed`` (worker error). The ``result`` "
        "field is populated only when ``status='finished'``; ``error`` "
        "is populated only when ``status='failed'``."
    ),
    responses={404: {"description": "Unknown job_id"}, 503: _503},
)
async def sdk_memory_extract_job_status(job_id: str) -> SDKMemoryExtractJobStatus:
    if not _queue_avail or not _rq_available:
        raise HTTPException(
            status_code=503,
            detail="Async memory queue is not configured (rq not installed).",
        )

    from rq.exceptions import NoSuchJobError  # type: ignore[import-not-found]
    from rq.job import Job  # type: ignore[import-not-found]

    queue = get_memory_queue()
    try:
        job = Job.fetch(job_id, connection=queue.connection)
    except NoSuchJobError:
        raise HTTPException(status_code=404, detail=f"Unknown job_id: {job_id}")

    status = job.get_status()  # rq's canonical status string
    payload: dict = {
        "job_id": job_id,
        "status": status or "unknown",
        "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "ended_at": job.ended_at.isoformat() if job.ended_at else None,
        "result": None,
        "error": None,
    }
    if status == "finished" and job.result is not None:
        # The worker returns the same dict shape ``SDKMemoryExtractResponse``
        # describes — Pydantic re-validates here so any drift is loud.
        payload["result"] = SDKMemoryExtractResponse(**job.result).model_dump()
    elif status == "failed":
        payload["error"] = (job.exc_info or "").strip().split("\n")[-1] or "worker failure"

    return SDKMemoryExtractJobStatus(**payload)


@router.post(
    "/llm/complete",
    response_model=SDKLLMCompleteResponse,
    summary="Smart-routed LLM completion",
    description=(
        "Tier-aware LLM completion. Consumers describe the task "
        "(`task_type`, `cost_sensitivity`); the smart_router selects the "
        "best model from FREE / CHEAP / CAPABLE / RESEARCH / EXPERT tiers, "
        "preferring Ollama when available. Returns content plus the model "
        "actually used and an estimated cost-per-1K-tokens for budget tracking."
    ),
    responses={422: _422, 503: _503},
)
async def sdk_llm_complete(req: SDKLLMCompleteRequest) -> SDKLLMCompleteResponse:
    from core.routing.smart_router import (  # noqa: F401  (EXPERT_MODELS import for cost lookup)
        EXPERT_MODELS,
        BudgetUnsatisfiableError,
    )
    from core.utils.llm_client import route_and_call

    try:
        content, decision = await route_and_call(
            messages=req.messages,
            query=req.query or (req.messages[-1].get("content", "")[:200] if req.messages else ""),
            task_type=req.task_type,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            response_format=req.response_format,
            cost_sensitivity=req.cost_sensitivity,
            slo_budget_ms=req.slo_budget_ms,
        )
    except BudgetUnsatisfiableError as exc:
        # Fail fast so the caller can route to direct providers — silently
        # downgrading the tier would hide a quality drop. Retry-After is in
        # seconds (rounded up from ms) per RFC 7231 § 7.1.3.
        retry_after_s = max(1, (exc.retry_after_ms + 999) // 1000)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "slo_budget_exceeded",
                "message": str(exc),
                "retry_after_ms": exc.retry_after_ms,
                "floor_p95_ms": exc.floor_p95_ms,
                "eligible_tier": exc.eligible_tier,
            },
            headers={"Retry-After": str(retry_after_s)},
        )
    return SDKLLMCompleteResponse(
        content=content,
        model=decision.model,
        provider=decision.provider,
        reason=decision.reason,
        estimated_cost_per_1k=decision.estimated_cost_per_1k,
        tier_p95_ms=decision.tier_p95_ms,
    )


@router.get(
    "/health",
    response_model=SDKHealthResponse,
    summary="Health Check",
    description="Service connectivity and consumer-relevant feature flags. "
    "Returns 'healthy' when all services are connected, 'degraded' otherwise.",
)
def sdk_health():
    from config.features import FEATURE_TOGGLES

    base = health_check()
    base["version"] = SDK_VERSION
    base["features"] = {
        k: v for k, v in FEATURE_TOGGLES.items()
        if k in (
            "enable_hallucination_check",
            "enable_feedback_loop",
            "enable_self_rag",
            "enable_memory_extraction",
        )
    }
    # Expose internal LLM provider info for SDK consumers
    import os
    base["internal_llm"] = {
        "provider": config.INTERNAL_LLM_PROVIDER,
        "model": config.INTERNAL_LLM_MODEL or config.OLLAMA_DEFAULT_MODEL,
        "ollama_enabled": os.getenv("OLLAMA_ENABLED", "false").lower() in ("true", "1"),
    }
    return base


# ---------------------------------------------------------------------------
# Ingest endpoints
# ---------------------------------------------------------------------------


@router.post("/ingest", summary="Ingest Text", responses={422: _422, 503: _503})
async def sdk_ingest(req: dict, request: Request):
    # Preserve client-supplied provenance metadata (GA P0.2): external clients
    # pass rich metadata (title / provenance / source_file / …). Keep the
    # legacy `tags` field alongside it rather than overwriting with tags-only.
    metadata = dict(req.get("metadata") or {})
    metadata.setdefault("tags", req.get("tags", ""))
    # Idempotency-Key (GA P0.5 D1): a retried ingest with the same key does not
    # double-write. No-op when the header is absent.
    return await idempotent(
        request,
        lambda: ingest_content(
            req.get("content", ""),
            domain=req.get("domain", "general"),
            metadata=metadata,
        ),
    )


@router.post("/ingest/file", summary="Ingest File", responses={422: _422, 503: _503})
async def sdk_ingest_file(req: dict):
    result = await ingest_file(
        req.get("file_path", ""),
        domain=req.get("domain", ""),
        tags=req.get("tags", ""),
    )
    return result


@router.post(
    "/ingest/external",
    response_model=IngestResult,
    summary="Generic external ingest",
    description=(
        "Accept an arbitrary JSON payload from any external service and ingest "
        "its content into the Cerid knowledge base.  The caller supplies a "
        "``field_mappings`` config that declares how to extract canonical fields "
        "(content, source URI, timestamp, tags, title, external ID) from the "
        "raw ``payload``.  A single payload can map to N ingest items via array "
        "fan-out (e.g. ``highlights[].text``).  "
        "The ``source_type`` label is stored as provenance metadata and is never "
        "branched on in code — this endpoint is generic and not special-cased "
        "for any particular service.  "
        "See ``docs/INTEGRATION_GUIDE.md`` for per-service mapping examples "
        "(Readwise, Pocket, Instapaper, Raindrop, Telegram-bot)."
    ),
    responses={422: _422, 503: _503},
)
async def sdk_ingest_external(request: ExternalIngestRequest, http_request: Request) -> IngestResult:
    from core.context.identity import get_tenant_id

    tenant = get_tenant_id()
    # Idempotency-Key (GA P0.5 D1): `request` here is the body model; headers
    # live on the FastAPI `http_request`. No-op without the header.
    return await idempotent(http_request, lambda: ingest_external(request, tenant=tenant))


# ---------------------------------------------------------------------------
# Webhook receiver — generic inbound HTTP endpoint per (:Source)
# ---------------------------------------------------------------------------


@router.post(
    "/ingest/webhook/{token}",
    summary="Token-gated webhook receiver",
    description=(
        "Generic inbound endpoint for any external service. The ``{token}`` "
        "path segment identifies a previously-created webhook-kind Source "
        "record whose ``config`` carries the matching token + optional HMAC "
        "secret. Returns ``202 Accepted`` immediately and queues the payload "
        "for async processing.\n\n"
        "**Authentication:** the token itself is the credential. For higher "
        "assurance, configure an HMAC secret on the source and require "
        "``X-Cerid-Signature: sha256=<hex>`` on every request; mismatched "
        "signatures return 401.\n\n"
        "**Adapter routing:** payloads can carry the canonical fields "
        "directly OR the source's config can declare per-source "
        "``field_mappings`` (Readwise / Pocket / Slack / GitHub-events / "
        "etc.) that extract them from a third-party-shaped payload."
    ),
    responses={
        404: {"description": "Unknown webhook token"},
        401: {"description": "HMAC signature missing or invalid"},
    },
    status_code=202,
)
async def sdk_ingest_webhook(token: str, request: Request) -> dict[str, str]:
    """Token-gated webhook receiver. See module docstring."""
    import json as _json

    from app.db.neo4j import sources as srcdb
    from app.deps import get_neo4j, get_redis
    from app.services.webhook_tokens import (
        find_webhook_source,
        verify_hmac_signature,
    )
    from core.utils.swallowed import log_swallowed_error
    from core.utils.time import utcnow_iso

    driver = get_neo4j()

    # Resolve the webhook source by token.
    source = find_webhook_source(driver, token)
    if source is None:
        raise HTTPException(status_code=404, detail="Unknown webhook token")

    # Read body once; HMAC + ingestion both need it.
    body = await request.body()

    # Optional HMAC verification when the source's config declares a secret.
    config = source.get("config", {}) or {}
    secret = config.get("hmac_secret") if isinstance(config, dict) else None
    signature_header = request.headers.get("X-Cerid-Signature", "")
    if secret and not verify_hmac_signature(secret, body, signature_header):
        raise HTTPException(
            status_code=401,
            detail="HMAC signature missing or invalid",
        )

    try:
        payload = _json.loads(body.decode("utf-8")) if body else {}
    except (UnicodeDecodeError, _json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Malformed JSON: {exc}")

    source_id = source["id"]

    # Adapter-recipe routing. If the source declares a provider
    # (e.g., kind=chat_capture + provider=slack), look up the
    # matching recipe and normalize the payload into one or more
    # CanonicalArtifact records before enqueue. Falls back to raw
    # payload pass-through when no recipe is registered.
    try:
        srcdb.update_source_status(driver, source_id, status="connected")
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "sdk_ingest_webhook.update_status",
            exc,
            context={"source_id": source_id},
        )

    provider = (config.get("provider") or "").strip() if isinstance(config, dict) else ""
    normalized: list[dict] | None = None
    requires_sig = False
    if provider:
        try:
            # Side-effect-imports the adapter package (registers recipes).
            import core.ingest.adapters as _adapters  # noqa: F401
            from core.ingest.adapters.registry import get_recipe

            recipe = get_recipe(source["kind"], provider)
            if recipe is not None:
                requires_sig = bool(getattr(recipe, "requires_signature", False))
                artifacts = recipe.fn(payload, config or {})
                normalized = [
                    {
                        "title": a.title,
                        "content": a.content,
                        "url": a.url,
                        "timestamp": a.timestamp,
                        "provider": a.provider,
                        "raw": a.raw,
                    }
                    for a in artifacts
                ]
        except Exception as exc:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "sdk_ingest_webhook.recipe",
                exc,
                context={"source_id": source_id, "provider": provider},
            )

    # Enforce the recipe's signature mandate: a provider whose recipe declares
    # requires_signature (e.g. github, stripe) MUST have an hmac_secret on the
    # source to verify against — otherwise the receiver silently accepts
    # unauthenticated payloads. The HMAC value-check above only fires when a
    # secret is present; this closes the "mandated but unconfigured" gap.
    if requires_sig and not secret:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Provider {provider!r} requires HMAC signing; configure an "
                "hmac_secret on the source before sending webhooks."
            ),
        )

    try:
        redis_client = get_redis()
        if redis_client is not None:
            redis_client.rpush(
                f"cerid:webhook_inbox:{source_id}",
                _json.dumps(
                    {
                        "received_at": utcnow_iso(),
                        "payload": payload,
                        "normalized": normalized,
                    },
                ),
            )
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "sdk_ingest_webhook.enqueue",
            exc,
            context={"source_id": source_id},
        )

    return {
        "status": "accepted",
        "source_id": source_id,
        "normalized_count": str(len(normalized)) if normalized is not None else "0",
    }


# ---------------------------------------------------------------------------
# Voice-note ingest — multipart upload → transcript → artifact
# ---------------------------------------------------------------------------


@router.post(
    "/ingest/voice-note",
    summary="Voice-note transcribe + ingest",
    description=(
        "Accepts a short audio clip (WAV / WebM / M4A — anything ffmpeg can "
        "decode), transcribes it with the bundled whisper.cpp pipeline, and "
        "ingests the transcript as an artifact linked to a voice_note Source.\n\n"
        "Recommended clip length: 30 s – 5 min. Larger clips should route "
        "through the Meeting Capture pipeline (``/meetings``) instead."
    ),
    responses={
        422: {"description": "Invalid audio payload"},
        500: {"description": "Transcription pipeline failure"},
        501: {"description": "Whisper runtime deps not installed"},
    },
    status_code=201,
)
async def sdk_ingest_voice_note(request: Request) -> dict:
    """Multipart upload → transcript → artifact.

    The endpoint is *synchronous*: the wizard waits for the transcript
    so it can pulse the duration and surface a snippet in the F11
    overlay's result step. Long-running transcription (>10s) is the
    caller's signal to switch to the Meeting Capture pipeline.
    """
    import tempfile
    import time as _time
    from pathlib import Path as _Path

    # Multipart parse — FastAPI's File()/Form() dependency injection
    # works but Request.form() is simpler when we only need one field.
    form = await request.form()
    audio = form.get("audio")
    if audio is None or not hasattr(audio, "read"):
        raise HTTPException(status_code=422, detail="missing 'audio' multipart field")

    suffix = _Path(getattr(audio, "filename", "voice.wav")).suffix or ".wav"
    started = _time.perf_counter()

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await audio.read())
        tmp_path = _Path(tmp.name)

    try:
        # Reuse the meeting_capture transcription path — whisper.cpp via
        # pywhispercpp. The plugin is internal-only; community builds
        # receive 501 with installation guidance.
        try:
            from plugins.meeting_capture import decode, transcribe
        except ImportError as exc:
            raise HTTPException(
                status_code=501,
                detail=(
                    f"voice-note transcription unavailable: {exc}. "
                    "Install meeting_capture plugin deps (pywhispercpp + ffmpeg)."
                ),
            ) from exc

        try:
            pcm_path = await asyncio.to_thread(decode.to_pcm16, tmp_path)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"audio decode failed: {exc}",
            ) from exc

        try:
            transcript_result = await asyncio.to_thread(transcribe.transcribe_pcm, pcm_path)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"transcription failed: {exc}",
            ) from exc

        text = (transcript_result.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="transcript was empty")

        # Ingest as a fresh artifact in the general domain. Metadata
        # carries the voice_note marker so retrieval can filter on it.
        from app.services.ingestion import ingest_content as _ingest_content

        ingest_result = await asyncio.to_thread(
            _ingest_content,
            text,
            "general",
            {
                "kind": "voice_note",
                "ingest_source": "voice_note_endpoint",
                "duration_words": len(text.split()),
            },
            skip_quality=False,
        )

        elapsed_ms = int((_time.perf_counter() - started) * 1000)
        return {
            "status": "ingested",
            "artifact_id": ingest_result.get("artifact_id"),
            "transcript": text,
            "transcribe_ms": elapsed_ms,
            "word_count": len(text.split()),
        }
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Collections / Taxonomy / Search
# ---------------------------------------------------------------------------


@router.get("/collections", summary="List Collections")
def sdk_collections():
    return list_collections()


@router.get("/taxonomy", summary="Domain Taxonomy")
def sdk_taxonomy():
    return {"domains": list(DOMAINS), "taxonomy": dict(TAXONOMY)}


@router.get("/health/detailed", summary="Detailed Health")
def sdk_health_detailed():
    return degradation_status()


@router.get("/settings", summary="SDK Settings")
def sdk_settings():
    return {"version": SDK_VERSION, "tier": FEATURE_TIER, "features": dict(FEATURE_FLAGS)}


@router.post("/search", summary="KB Search", responses={422: _422, 503: _503})
def sdk_search(req: dict):
    result = query_knowledge(
        req.get("query", ""),
        domain=req.get("domain", "general"),
        top_k=req.get("top_k", 3),
    )
    sources = result.get("sources", [])
    return {"results": sources, "total_results": len(sources), "confidence": result.get("confidence", 0.0)}


@router.get("/plugins", summary="List Plugins")
def sdk_plugins():
    result = list_plugins()
    return {"plugins": [p.model_dump() for p in result.plugins], "total": result.total}
