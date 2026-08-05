# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""CRUD and query endpoints for user-defined custom agents (Phase 3 — extensibility).

Prefix: ``/custom-agents`` (avoids collision with the built-in ``/agents`` router).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.services.strict_agents_policy import enforce_strict_mode
from deps import get_chroma, get_graph_store, get_neo4j, get_redis
from models.agents import (
    AgentCreateRequest,
    AgentDefinition,
    AgentListResponse,
    AgentQueryRequest,
    AgentTemplateOverrides,
    AgentUpdateRequest,
)


# --- Response models (generated: single-return dict-literal routes) ---
class DeleteAgentResponse(BaseModel):
    deleted: bool
    agent_id: Any


class ListTemplatesResponse(BaseModel):
    templates: Any



# Sprint 1C — STRICT_AGENTS_ONLY=true blocks every endpoint here with 403
# before the body executes. Default off; regulated deployments turn it on.
router = APIRouter(
    tags=["custom-agents"],
    dependencies=[Depends(enforce_strict_mode)],
)
logger = logging.getLogger("ai-companion.custom_agents")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@router.get("/custom-agents/templates", response_model=ListTemplatesResponse)
async def list_templates():
    """Return all built-in agent templates."""
    from app.agents.templates import list_templates as _list

    return {"templates": _list()}


@router.post("/custom-agents/from-template/{template_id}", status_code=201, response_model=AgentDefinition)
async def create_from_template(
    template_id: str,
    overrides: AgentTemplateOverrides | None = None,
):
    """Create a new custom agent pre-filled from a built-in template.

    Any fields provided in the optional *overrides* body replace the template defaults.
    """
    from app.agents.templates import get_template
    from app.db.neo4j.agents import create_agent

    tpl = get_template(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")

    # Start from template values, overlay user overrides
    params: dict = {
        "name": tpl["name"],
        "description": tpl["description"],
        "system_prompt": tpl["system_prompt"],
        "tools": tpl.get("tools", []),
        "domains": tpl.get("domains", []),
        "rag_mode": tpl.get("rag_mode", "smart"),
        "temperature": tpl.get("temperature", 0.7),
        "template_id": template_id,
    }

    if overrides:
        override_data = overrides.model_dump(exclude_unset=True)
        params.update(override_data)

    driver = get_neo4j()
    agent = create_agent(driver, **params)
    return agent


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("/custom-agents")  # response-model-allowed: dynamic response (shape varies)
async def list_agents(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List all custom agents (newest first)."""
    from app.db.neo4j.agents import count_agents as _count
    from app.db.neo4j.agents import list_agents as _list

    driver = get_neo4j()
    agents = _list(driver, offset=offset, limit=limit)
    total = _count(driver)
    return AgentListResponse(
        agents=[AgentDefinition(**a) if isinstance(a, dict) else a for a in agents],
        total=total,
    )


@router.post("/custom-agents", status_code=201, response_model=AgentDefinition)
async def create_agent(body: AgentCreateRequest):
    """Create a new custom agent from scratch."""
    from app.db.neo4j.agents import create_agent as _create

    driver = get_neo4j()
    agent = _create(driver, **body.model_dump())
    return agent


@router.get("/custom-agents/{agent_id}", response_model=AgentDefinition)
async def get_agent(agent_id: str):
    """Retrieve a single custom agent by ID."""
    from app.db.neo4j.agents import get_agent as _get

    driver = get_neo4j()
    agent = _get(driver, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.patch("/custom-agents/{agent_id}", response_model=AgentDefinition)
async def update_agent(agent_id: str, body: AgentUpdateRequest):
    """Partially update a custom agent (PATCH semantics)."""
    from app.db.neo4j.agents import update_agent as _update

    driver = get_neo4j()
    fields = body.model_dump(exclude_unset=True)
    updated = _update(driver, agent_id, **fields)
    if not updated:
        raise HTTPException(status_code=404, detail="Agent not found")
    return updated


@router.delete("/custom-agents/{agent_id}", response_model=DeleteAgentResponse)
async def delete_agent(agent_id: str):
    """Delete a custom agent by ID."""
    from app.db.neo4j.agents import delete_agent as _delete

    driver = get_neo4j()
    deleted = _delete(driver, agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"deleted": True, "agent_id": agent_id}


# ---------------------------------------------------------------------------
# Query through agent
# ---------------------------------------------------------------------------


@router.post("/custom-agents/{agent_id}/query")  # response-model-allowed: dynamic response (shape varies)
async def query_agent(agent_id: str, body: AgentQueryRequest, request: Request):
    """Execute a query using a custom agent's configuration.

    Loads the agent definition, builds an overlay config (system prompt,
    domains, rag_mode, model), and delegates to the standard query agent.
    """
    from app.db.neo4j.agents import get_agent as _get

    driver = get_neo4j()
    agent = _get(driver, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # This endpoint returns a single retrieval result, not an SSE stream.
    # Reject stream=True rather than silently ignoring it (contract honesty);
    # streaming custom-agent queries can be wired here later if needed.
    if body.stream:
        raise HTTPException(
            status_code=501,
            detail="Streaming is not supported on custom-agent queries; use stream=false.",
        )

    # Delegate to the query agent through the guarded seam so Private Mode +
    # consumer isolation are enforced on this transport too (E1 Phase 1). E1
    # CR-095: honor the agent's configured rag_mode — a 'smart' agent (the stored
    # default) runs kb/memory/external orchestration via the guarded smart seam,
    # not the manual kb-only path it was previously stuck on regardless of config.
    from app.services.request_policy import build_request_context

    # E1 CR-087: resolve the caller's consumer identity so a restricted consumer
    # cannot escape its allowed_domains wall via a custom agent.
    request_context = build_request_context(client_id=request.headers.get("x-client-id", "gui"))
    rag_mode = agent.get("rag_mode", "smart")
    # Wire the full store set like every other answer-path caller. Without
    # neo4j_driver the post-retrieval active-learning join is skipped, so archived
    # (soft-deleted / quarantined) artifacts would still surface here — the
    # residual AF-001 hole on the /custom_agents path. This also restores semantic
    # cache + graph expansion this path was missing.
    _stores = dict(
        chroma_client=get_chroma(),
        redis_client=get_redis(),
        neo4j_driver=get_neo4j(),
        graph_store=get_graph_store(),
    )
    from app.concurrency import KB_POOL

    # E1 CR-096: gate under KB_POOL like /agent/query + A2A (CR-091) so unbounded
    # concurrent custom-agent retrieval — smart or manual — cannot starve the
    # /health + /observability routes the pool exists to protect.
    async with KB_POOL.acquire():
        if rag_mode in ("smart", "custom_smart"):
            from app.agents.retrieval_orchestrator import guarded_orchestrated_query
            result = await guarded_orchestrated_query(
                request_context=request_context,
                query=body.query,
                rag_mode=rag_mode,
                domains=agent.get("domains") or None,
                model=agent.get("model_override") or None,
                top_k=10,
                **_stores,
            )
        else:
            from core.agents.guarded_retrieval import guarded_agent_query_full
            result = await guarded_agent_query_full(
                request_context=request_context,
                query=body.query,
                domains=agent.get("domains") or None,
                model=agent.get("model_override") or None,
                top_k=10,
                **_stores,
            )
    # Attach agent context so the caller can apply system_prompt/temperature
    result["agent_config"] = {
        "system_prompt": agent.get("system_prompt", ""),
        "temperature": agent.get("temperature", 0.7),
        "rag_mode": agent.get("rag_mode", "smart"),
        "tools": agent.get("tools", []),
    }
    return result
