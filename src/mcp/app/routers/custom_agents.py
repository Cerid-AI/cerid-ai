# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""CRUD and query endpoints for user-defined custom agents (Phase 3 — extensibility).

Prefix: ``/custom-agents`` (avoids collision with the built-in ``/agents`` router).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from deps import get_neo4j
from models.agents import (
    AgentCreateRequest,
    AgentDefinition,
    AgentListResponse,
    AgentQueryRequest,
    AgentUpdateRequest,
)

router = APIRouter(tags=["custom-agents"])
logger = logging.getLogger("ai-companion.custom_agents")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@router.get("/custom-agents/templates")
async def list_templates():
    """Return all built-in agent templates."""
    from app.agents.templates import list_templates as _list

    return {"templates": _list()}


@router.post("/custom-agents/from-template/{template_id}", status_code=201, response_model=AgentDefinition)
async def create_from_template(
    template_id: str,
    overrides: AgentCreateRequest | None = None,
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


@router.get("/custom-agents")
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


@router.delete("/custom-agents/{agent_id}")
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


@router.post("/custom-agents/{agent_id}/query")
async def query_agent(agent_id: str, body: AgentQueryRequest):
    """Execute a query using a custom agent's configuration.

    Loads the agent definition, builds an overlay config (system prompt,
    domains, rag_mode, model), and delegates to the standard query agent.
    """
    from app.db.neo4j.agents import get_agent as _get

    driver = get_neo4j()
    agent = _get(driver, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Delegate to the query agent with the custom agent's configuration
    from core.agents.query_agent import agent_query

    result = await agent_query(
        query=body.query,
        domains=agent.get("domains") or None,
        model=agent.get("model_override") or None,
        top_k=body.top_k if hasattr(body, "top_k") else 10,
    )
    # Attach agent context so the caller can apply system_prompt/temperature
    result["agent_config"] = {
        "system_prompt": agent.get("system_prompt", ""),
        "temperature": agent.get("temperature", 0.7),
        "rag_mode": agent.get("rag_mode", "smart"),
        "tools": agent.get("tools", []),
    }
    return result
