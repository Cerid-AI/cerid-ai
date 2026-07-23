# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""A2A (Agent-to-Agent) Protocol — Phase 45.

Implements the Google A2A spec so cerid is discoverable and invokable by
other AI agents.  The Agent Card lives at ``/.well-known/agent.json`` and
the task lifecycle endpoints live under ``/a2a/``.

Each A2A skill maps to an existing cerid agent call — no new business logic,
just a thin protocol adapter with Redis-backed task state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.deps import get_chroma, get_graph_store, get_neo4j, get_redis
from core.utils.swallowed import log_swallowed_error
from core.utils.version import get_version


# --- Response models (generated: single-return dict-literal routes) ---
class AgentCardResponse(BaseModel):
    name: str
    description: str
    url: Any
    version: str
    capabilities: dict
    skills: list
    authentication: dict
    defaultInputModes: list
    defaultOutputModes: list



logger = logging.getLogger("ai-companion.a2a")

router = APIRouter(tags=["a2a"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TASK_TTL = 3600  # 1 hour
_REDIS_PREFIX = "cerid:a2a:tasks:"
_HISTORY_PREFIX = "cerid:a2a:history:"

# E1 CR-034: in-flight skill executions keyed by task_id, so cancel_task can
# actually stop a running task (asyncio cancellation) instead of setting a
# 'canceled' flag the still-running inline executor overwrites. Process-local by
# design — a task is bound to the worker that created it.
_running_tasks: dict[str, "asyncio.Task[Any]"] = {}

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class A2ATaskRequest(BaseModel):
    skill_id: str = Field(..., description="Skill to invoke (e.g. 'knowledge-query')")
    input: dict = Field(..., description="Skill input payload")
    metadata: dict = Field(default_factory=dict)


class A2ATask(BaseModel):
    id: str
    skill_id: str
    status: str  # submitted | working | completed | failed | canceled
    # E1 R1: Private Mode L1+ redacts input/output (and metadata) in the durable
    # Redis copy via ``_persist_view``; GET/cancel load that copy so these must
    # accept null — a required ``dict`` 500'd after CR-085 landed.
    input: dict | None = None
    output: dict | None = None
    error: str | None = None
    created_at: str
    updated_at: str
    # E1 CR-033: persist the caller's correlation metadata so it is recoverable
    # from task state / history (accepted by A2ATaskRequest but previously dropped).
    # E1 R12: redacted to null at L1+ alongside input/output.
    metadata: dict | None = Field(default_factory=dict)


class A2ATaskHistory(BaseModel):
    transitions: list[dict]


# --- Validated per-skill input models (E1 CR-033) ---------------------------
# A2ATaskRequest.input is an unvalidated dict; each skill parses it through a
# typed model so the FULL REST-equivalent knob set is marshalled (not a hardcoded
# subset), with peer-friendly ``text``/``query`` aliases.


class KnowledgeQueryInput(BaseModel):
    """A2A knowledge-query surface — the common REST /agent/query knobs.

    Not full parity with every REST field (no rag_mode/context_sources/etc.);
    peers use REST for the complete contract. Bounds match the REST top_k
    envelope so an unbounded A2A caller cannot force an unbounded retrieval.
    """
    model_config = ConfigDict(populate_by_name=True, extra="ignore",
                              protected_namespaces=())

    query: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("text", "query"),
        description="Natural-language query (aliases: text, query)",
    )
    domains: list[str] | None = None
    top_k: int = Field(10, ge=1, le=100)
    use_reranking: bool = True
    conversation_messages: list[dict] | None = None
    skip_cache: bool = False
    metadata_filter: dict | None = None
    exclude_packs: bool = False
    strict_domains: bool = False
    budget_seconds: float | None = Field(default=None, ge=0.1, le=300.0)
    model: str | None = None


class VerificationInput(BaseModel):
    """Full verification parameter surface — parity with REST /agent/hallucination."""
    model_config = ConfigDict(populate_by_name=True, extra="ignore",
                              protected_namespaces=())

    response_text: str = Field("", validation_alias=AliasChoices("text", "response_text"))
    conversation_id: str = "a2a"
    threshold: float | None = None
    model: str | None = None
    user_query: str | None = None
    expert_mode: bool = False


# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------


@router.get("/.well-known/agent.json", response_model=AgentCardResponse)
async def agent_card():
    """A2A Agent Card — advertises cerid's capabilities to other agents."""
    return {
        "name": "Cerid AI",
        "description": (
            "Privacy-first Personal AI Knowledge Companion with RAG, "
            "verification, and multi-domain knowledge management"
        ),
        "url": f"http://localhost:{os.getenv('CERID_PORT_MCP', '8888')}",
        # E1 CR-034: single-source the version from pyproject, not a hardcode.
        "version": get_version(),
        "capabilities": {
            # Honest: every A2A skill returns a buffered dict (no SSE). Advertise
            # false until real streaming lands (audit 2026-06-29, STREAM/quick-win).
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "skills": [
            {
                "id": "knowledge-query",
                "name": "Knowledge Query",
                "description": (
                    "Query the personal knowledge base with RAG-enhanced "
                    "retrieval, cross-encoder reranking, and hallucination "
                    "verification"
                ),
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "document-ingest",
                "name": "Document Ingestion",
                "description": (
                    "Ingest documents into the knowledge base with automatic "
                    "categorization, chunking, and graph relationships"
                ),
                "inputModes": ["text", "file"],
                "outputModes": ["text"],
            },
            {
                "id": "memory-recall",
                "name": "Memory Recall",
                "description": "Recall contextual memories with decay-adjusted scoring",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "web-search",
                "name": "Web Search",
                "description": "Search the web with Self-RAG verification",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "hallucination-check",
                "name": "Hallucination Verification",
                "description": (
                    "Verify LLM responses against the knowledge base with "
                    "4 claim types"
                ),
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
        ],
        "authentication": {
            "schemes": [
                {"scheme": "apiKey", "in": "header", "name": "X-API-Key"},
            ],
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
    }


# ---------------------------------------------------------------------------
# Skill-to-agent mapping
# ---------------------------------------------------------------------------


async def _execute_query(input_data: dict) -> dict:
    """Wrap the agent query pipeline."""
    from app.concurrency import KB_POOL
    from app.services.request_policy import build_request_context
    from core.agents.guarded_retrieval import guarded_agent_query_full

    # E1 CR-033: marshal the FULL query knob set from a validated input model,
    # not a hardcoded subset — peers get parity with REST /agent/query.
    params = KnowledgeQueryInput.model_validate(input_data)

    # E1 CR-091: gate under KB_POOL like REST /agent/query so unbounded
    # concurrent A2A retrieval cannot starve the lightweight /health +
    # /observability routes.
    async with KB_POOL.acquire():
        result = await guarded_agent_query_full(
            request_context=build_request_context(
                client_id="a2a-agent",
                skip_cache=params.skip_cache,
                metadata_filter=params.metadata_filter,
                budget_seconds=params.budget_seconds,
                strict_domains=params.strict_domains,
            ),
            query=params.query,
            domains=params.domains,
            top_k=params.top_k,
            use_reranking=params.use_reranking,
            conversation_messages=params.conversation_messages,
            chroma_client=get_chroma(),
            neo4j_driver=get_neo4j(),
            redis_client=get_redis(),
            # E1 CR-025: without graph_store, graph expansion + quality/summary
            # enrichment silently skip on this transport — parity with /agent/query.
            graph_store=get_graph_store(),
            model=params.model,
            exclude_packs=params.exclude_packs,
        )
    return result


async def _execute_ingest(input_data: dict) -> dict:
    """Wrap content ingestion.

    E1 CR-091: ``ingest_content`` is a blocking (sync) call; offload it to a
    thread under the ingest semaphore so a large A2A document never blocks the
    event loop for the full embed/chunk/Neo4j-write duration (matching the REST
    ``/ingest`` path), then invalidate the query cache so the new content is
    retrievable.
    """
    from app.services.ingestion import _ingest_semaphore, ingest_content

    async with _ingest_semaphore:
        result = await asyncio.to_thread(
            ingest_content,
            input_data.get("text", input_data.get("content", "")),
            input_data.get("domain", "general"),
        )
    try:
        from utils.query_cache import invalidate_cache_non_blocking
        asyncio.get_running_loop().create_task(invalidate_cache_non_blocking())
    except Exception as e:
        log_swallowed_error("routers.a2a.ingest_cache_invalidate", e)
    return result


async def _execute_recall(input_data: dict) -> dict:
    """Wrap memory recall with decay scoring."""
    from app.services.request_policy import build_request_context
    from core.agents.guarded_retrieval import guarded_recall_memories

    memories = await guarded_recall_memories(
        request_context=build_request_context(client_id="a2a-agent"),
        query=input_data.get("text", input_data.get("query", "")),
        chroma_client=get_chroma(),
        neo4j_driver=get_neo4j(),
        top_k=input_data.get("top_k", 10),
    )
    return {"memories": memories, "count": len(memories)}


async def _execute_web_search(input_data: dict) -> dict:
    """Wrap web search with Self-RAG verification."""
    from utils.web_search import search_and_verify

    result = await search_and_verify(
        query=input_data.get("text", input_data.get("query", "")),
        chroma_client=get_chroma(),
        neo4j_driver=get_neo4j(),
        redis_client=get_redis(),
    )
    return result


async def _execute_verification(input_data: dict) -> dict:
    """Wrap hallucination checking."""
    from app.services.private_mode import saves_blocked
    from core.agents.hallucination import check_hallucinations

    # E1 CR-033: marshal the full verifier knob set (threshold/model/user_query/
    # expert_mode) like REST /agent/hallucination, not just response_text.
    params = VerificationInput.model_validate(input_data)

    result = await check_hallucinations(
        response_text=params.response_text,
        conversation_id=params.conversation_id,
        chroma_client=get_chroma(),
        neo4j_driver=get_neo4j(),
        redis_client=get_redis(),
        threshold=params.threshold,
        model=params.model,
        user_query=params.user_query,
        expert_mode=params.expert_mode,
        # Private Mode L1+ suppresses the durable hall:{cid} report on the A2A
        # transport too, matching the REST handlers (CR-018/086).
        persist_report=not saves_blocked(),
    )
    return result


SKILL_MAP: dict[str, Callable[[dict], Awaitable[dict]]] = {
    "knowledge-query": _execute_query,
    "document-ingest": _execute_ingest,
    "memory-recall": _execute_recall,
    "web-search": _execute_web_search,
    "hallucination-check": _execute_verification,
}

# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# E1 CR-085: Private Mode L1+ ("skip saves") must not durably store
# conversation-derived data. The A2A task record persists the caller's input and
# the skill's retrieval/verification output to Redis (recoverable for an hour via
# GET /a2a/tasks/{id}) — the same class of durable store the sibling hall:{cid}
# write already gates via ``persist_report=not saves_blocked()``. Nothing gated
# the task record, so at L1+ an A2A round-trip both retrieved real KB/memory
# content AND durably stored it. Redact those payload fields from the persisted
# copy; ``create_task`` still returns the real result inline, so the caller is
# unaffected — only the durable Redis copy is stripped.
# E1 R12: metadata is the same conversation-derived provenance class as input —
# redact it at L1+ so Private Mode "skip saves" is not bypassed via side channels.
_REDACTED_TASK_FIELDS = ("input", "output", "metadata")


def _persist_view(task: dict) -> dict:
    """The task dict to durably persist — payload-redacted under Private Mode L1+."""
    from app.services.private_mode import saves_blocked

    if not saves_blocked():
        return task
    redacted = dict(task)
    for field in _REDACTED_TASK_FIELDS:
        if redacted.get(field) is not None:
            redacted[field] = None
    return redacted


def _save_task(task: dict) -> None:
    """Persist task dict to Redis with TTL."""
    r = get_redis()
    key = f"{_REDIS_PREFIX}{task['id']}"
    r.set(key, json.dumps(_persist_view(task)), ex=_TASK_TTL)


def _load_task(task_id: str) -> dict | None:
    """Load task dict from Redis."""
    r = get_redis()
    raw = r.get(f"{_REDIS_PREFIX}{task_id}")
    if raw is None:
        return None
    return json.loads(raw)


def _append_history(task_id: str, status: str) -> None:
    """Append a status transition to the task history list."""
    r = get_redis()
    key = f"{_HISTORY_PREFIX}{task_id}"
    entry = json.dumps({"status": status, "at": _now_iso()})
    r.rpush(key, entry)
    r.expire(key, _TASK_TTL)


def _get_history(task_id: str) -> list[dict]:
    """Retrieve full transition history for a task."""
    r = get_redis()
    raw_list = r.lrange(f"{_HISTORY_PREFIX}{task_id}", 0, -1)
    return [json.loads(item) for item in raw_list]


def _transition(task: dict, new_status: str, **extra: object) -> dict:
    """Transition task to a new status, persist, and log history."""
    task["status"] = new_status
    task["updated_at"] = _now_iso()
    task.update(extra)
    _save_task(task)
    _append_history(task["id"], new_status)
    return task


# ---------------------------------------------------------------------------
# Task lifecycle endpoints
# ---------------------------------------------------------------------------


@router.post("/a2a/tasks", response_model=A2ATask)
async def create_task(request: A2ATaskRequest):
    """Create and execute a new A2A task.

    Maps ``skill_id`` to the corresponding cerid agent call, executes it,
    and returns the completed (or failed) task.
    """
    if request.skill_id not in SKILL_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown skill_id: {request.skill_id}. "
            f"Available: {', '.join(SKILL_MAP)}",
        )

    task_id = str(uuid.uuid4())
    now = _now_iso()

    task: dict = {
        "id": task_id,
        "skill_id": request.skill_id,
        "status": "submitted",
        "input": request.input,
        "output": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
        # E1 CR-033: persist the caller's correlation metadata for cross-agent
        # tracing (recoverable via GET /a2a/tasks/{id} and history).
        "metadata": request.metadata,
    }
    _save_task(task)
    _append_history(task_id, "submitted")

    # Transition to working
    _transition(task, "working")

    # E1 CR-034: run the skill as a registered, cancellable asyncio task rather
    # than a bare inline await, so a concurrent cancel_task can actually stop it.
    executor = SKILL_MAP[request.skill_id]
    skill_task = asyncio.ensure_future(executor(request.input))
    _running_tasks[task_id] = skill_task
    try:
        result = await skill_task
        _transition(task, "completed", output=result)
    except asyncio.CancelledError:
        # cancel_task cancelled the executor (and already logged + persisted the
        # 'canceled' transition). Sync this handler's task copy and return it —
        # never fall through to completed/failed, and never re-raise (create_task
        # itself was not cancelled, only the inner skill).
        task["status"] = "canceled"
        task["updated_at"] = _now_iso()
        _save_task(task)
    except Exception as exc:
        logger.exception("A2A task %s failed: %s", task_id, exc)
        _transition(task, "failed", error=str(exc))
    finally:
        _running_tasks.pop(task_id, None)

    return task


@router.get("/a2a/tasks/{task_id}", response_model=A2ATask)
async def get_task(task_id: str):
    """Get task status and result."""
    task = _load_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found or expired")
    return task


@router.post("/a2a/tasks/{task_id}/cancel", response_model=A2ATask)
async def cancel_task(task_id: str):
    """Cancel a running task.

    Only tasks in ``submitted`` or ``working`` status can be canceled.
    Already-completed or failed tasks return 409.
    """
    task = _load_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found or expired")

    if task["status"] in ("completed", "failed", "canceled"):
        raise HTTPException(
            status_code=409,
            detail=f"Task already in terminal state: {task['status']}",
        )

    # E1 CR-034: actually stop the running executor so it cannot overwrite the
    # canceled state with completed/failed. Cooperative — the skill sees a
    # CancelledError at its next await point.
    skill_task = _running_tasks.get(task_id)
    if skill_task is not None and not skill_task.done():
        skill_task.cancel()

    _transition(task, "canceled")
    return task


@router.get("/a2a/tasks/{task_id}/history", response_model=A2ATaskHistory)
async def get_task_history(task_id: str):
    """Get the full status transition history for a task."""
    task = _load_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found or expired")

    transitions = _get_history(task_id)
    return {"transitions": transitions}
