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

import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.deps import get_chroma, get_neo4j, get_redis

logger = logging.getLogger("ai-companion.a2a")

router = APIRouter(tags=["a2a"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TASK_TTL = 3600  # 1 hour
_REDIS_PREFIX = "cerid:a2a:tasks:"
_HISTORY_PREFIX = "cerid:a2a:history:"

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
    input: dict
    output: dict | None = None
    error: str | None = None
    created_at: str
    updated_at: str


class A2ATaskHistory(BaseModel):
    transitions: list[dict]


# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------


@router.get("/.well-known/agent.json")
async def agent_card():
    """A2A Agent Card — advertises cerid's capabilities to other agents."""
    return {
        "name": "Cerid AI",
        "description": (
            "Privacy-first Personal AI Knowledge Companion with RAG, "
            "verification, and multi-domain knowledge management"
        ),
        "url": f"http://localhost:{os.getenv('CERID_PORT_MCP', '8888')}",
        "version": "2.0.0",
        "capabilities": {
            "streaming": True,
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
    from agents.query_agent import agent_query

    result = await agent_query(
        query=input_data.get("text", input_data.get("query", "")),
        domains=input_data.get("domains"),
        top_k=input_data.get("top_k", 10),
        use_reranking=input_data.get("use_reranking", True),
        chroma_client=get_chroma(),
        neo4j_driver=get_neo4j(),
        redis_client=get_redis(),
    )
    return result


async def _execute_ingest(input_data: dict) -> dict:
    """Wrap content ingestion."""
    from app.services.ingestion import ingest_content

    result = ingest_content(
        input_data.get("text", input_data.get("content", "")),
        input_data.get("domain", "general"),
    )
    return result


async def _execute_recall(input_data: dict) -> dict:
    """Wrap memory recall with decay scoring."""
    from agents.memory import recall_memories

    memories = await recall_memories(
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
    from agents.hallucination import check_hallucinations

    result = await check_hallucinations(
        response_text=input_data.get("text", input_data.get("response_text", "")),
        conversation_id=input_data.get("conversation_id", "a2a"),
        chroma_client=get_chroma(),
        neo4j_driver=get_neo4j(),
        redis_client=get_redis(),
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


def _save_task(task: dict) -> None:
    """Persist task dict to Redis with TTL."""
    r = get_redis()
    key = f"{_REDIS_PREFIX}{task['id']}"
    r.set(key, json.dumps(task), ex=_TASK_TTL)


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
    }
    _save_task(task)
    _append_history(task_id, "submitted")

    # Transition to working
    _transition(task, "working")

    try:
        executor = SKILL_MAP[request.skill_id]
        result = await executor(request.input)
        _transition(task, "completed", output=result)
    except Exception as exc:
        logger.exception("A2A task %s failed: %s", task_id, exc)
        _transition(task, "failed", error=str(exc))

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
