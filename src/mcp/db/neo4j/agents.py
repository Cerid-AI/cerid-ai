# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j CRUD operations for custom agent definitions (Phase 3 — extensibility).

Stores agents as :CustomAgent nodes.  Follows the same driver/session pattern
used by ``db/neo4j/artifacts.py``.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.graph.agents")


def _row_to_dict(record) -> dict[str, Any]:
    """Convert a Neo4j record to a plain dict, deserialising JSON fields."""
    tools_raw = record.get("tools", "[]")
    domains_raw = record.get("domains", "[]")
    metadata_raw = record.get("metadata", "{}")

    try:
        tools = json.loads(tools_raw) if isinstance(tools_raw, str) else (tools_raw or [])
    except (json.JSONDecodeError, TypeError):
        tools = []
    try:
        domains = json.loads(domains_raw) if isinstance(domains_raw, str) else (domains_raw or [])
    except (json.JSONDecodeError, TypeError):
        domains = []
    try:
        metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else (metadata_raw or {})
    except (json.JSONDecodeError, TypeError):
        metadata = {}

    return {
        "agent_id": record["agent_id"],
        "name": record["name"],
        "description": record.get("description", ""),
        "system_prompt": record.get("system_prompt", ""),
        "tools": tools,
        "domains": domains,
        "rag_mode": record.get("rag_mode", "smart"),
        "model_override": record.get("model_override"),
        "temperature": record.get("temperature", 0.7),
        "metadata": metadata,
        "template_id": record.get("template_id"),
        "created_at": record.get("created_at", ""),
        "updated_at": record.get("updated_at", ""),
    }


def create_agent(
    driver,
    *,
    name: str,
    description: str = "",
    system_prompt: str = "",
    tools: list[str] | None = None,
    domains: list[str] | None = None,
    rag_mode: str = "smart",
    model_override: str | None = None,
    temperature: float = 0.7,
    metadata: dict[str, Any] | None = None,
    template_id: str | None = None,
) -> dict[str, Any]:
    """Create a :CustomAgent node and return the full record."""
    agent_id = str(uuid.uuid4())
    now = utcnow_iso()

    with driver.session() as session:
        result = session.run(
            """
            CREATE (a:CustomAgent {
                agent_id: $agent_id,
                name: $name,
                description: $description,
                system_prompt: $system_prompt,
                tools: $tools,
                domains: $domains,
                rag_mode: $rag_mode,
                model_override: $model_override,
                temperature: $temperature,
                metadata: $metadata,
                template_id: $template_id,
                created_at: $now,
                updated_at: $now
            })
            RETURN a.agent_id AS agent_id, a.name AS name,
                   a.description AS description, a.system_prompt AS system_prompt,
                   a.tools AS tools, a.domains AS domains,
                   a.rag_mode AS rag_mode, a.model_override AS model_override,
                   a.temperature AS temperature, a.metadata AS metadata,
                   a.template_id AS template_id,
                   a.created_at AS created_at, a.updated_at AS updated_at
            """,
            agent_id=agent_id,
            name=name,
            description=description,
            system_prompt=system_prompt,
            tools=json.dumps(tools or []),
            domains=json.dumps(domains or []),
            rag_mode=rag_mode,
            model_override=model_override,
            temperature=temperature,
            metadata=json.dumps(metadata or {}),
            template_id=template_id,
            now=now,
        )
        record = result.single()
        if not record:
            raise RuntimeError("Failed to create CustomAgent node")
        logger.info("Created custom agent %s (%s)", agent_id[:8], name)
        return _row_to_dict(record)


def get_agent(driver, agent_id: str) -> dict[str, Any] | None:
    """Fetch a single custom agent by ID."""
    with driver.session() as session:
        result = session.run(
            """
            MATCH (a:CustomAgent {agent_id: $agent_id})
            RETURN a.agent_id AS agent_id, a.name AS name,
                   a.description AS description, a.system_prompt AS system_prompt,
                   a.tools AS tools, a.domains AS domains,
                   a.rag_mode AS rag_mode, a.model_override AS model_override,
                   a.temperature AS temperature, a.metadata AS metadata,
                   a.template_id AS template_id,
                   a.created_at AS created_at, a.updated_at AS updated_at
            """,
            agent_id=agent_id,
        )
        record = result.single()
        if not record:
            return None
        return _row_to_dict(record)


def update_agent(
    driver,
    agent_id: str,
    **fields: Any,
) -> dict[str, Any] | None:
    """Update specific fields on an existing CustomAgent node.

    Only non-None values in *fields* are applied.  Returns the updated record
    or ``None`` if the agent was not found.
    """
    set_clauses: list[str] = ["a.updated_at = $now"]
    params: dict[str, Any] = {"agent_id": agent_id, "now": utcnow_iso()}

    # Map Python field names to Neo4j property names
    _json_fields = {"tools", "domains", "metadata"}
    for key, val in fields.items():
        if val is None:
            continue
        param_name = f"p_{key}"
        if key in _json_fields:
            params[param_name] = json.dumps(val)
        else:
            params[param_name] = val
        set_clauses.append(f"a.{key} = ${param_name}")

    if len(set_clauses) == 1:
        # Nothing to update beyond the timestamp
        return get_agent(driver, agent_id)

    query = (
        "MATCH (a:CustomAgent {agent_id: $agent_id}) "
        f"SET {', '.join(set_clauses)} "
        "RETURN a.agent_id AS agent_id, a.name AS name, "
        "a.description AS description, a.system_prompt AS system_prompt, "
        "a.tools AS tools, a.domains AS domains, "
        "a.rag_mode AS rag_mode, a.model_override AS model_override, "
        "a.temperature AS temperature, a.metadata AS metadata, "
        "a.template_id AS template_id, "
        "a.created_at AS created_at, a.updated_at AS updated_at"
    )

    with driver.session() as session:
        result = session.run(query, **params)
        record = result.single()
        if not record:
            return None
        logger.info("Updated custom agent %s", agent_id[:8])
        return _row_to_dict(record)


def delete_agent(driver, agent_id: str) -> bool:
    """Delete a custom agent by ID. Returns ``True`` if the node existed."""
    with driver.session() as session:
        result = session.run(
            "MATCH (a:CustomAgent {agent_id: $agent_id}) "
            "DETACH DELETE a "
            "RETURN count(a) AS deleted",
            agent_id=agent_id,
        )
        record = result.single()
        deleted = bool(record and record["deleted"] > 0)
        if deleted:
            logger.info("Deleted custom agent %s", agent_id[:8])
        return deleted


def list_agents(
    driver,
    offset: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List all custom agents, ordered by creation date (newest first)."""
    with driver.session() as session:
        result = session.run(
            """
            MATCH (a:CustomAgent)
            RETURN a.agent_id AS agent_id, a.name AS name,
                   a.description AS description, a.system_prompt AS system_prompt,
                   a.tools AS tools, a.domains AS domains,
                   a.rag_mode AS rag_mode, a.model_override AS model_override,
                   a.temperature AS temperature, a.metadata AS metadata,
                   a.template_id AS template_id,
                   a.created_at AS created_at, a.updated_at AS updated_at
            ORDER BY a.created_at DESC
            SKIP $offset LIMIT $limit
            """,
            offset=offset,
            limit=limit,
        )
        return [_row_to_dict(record) for record in result]


def list_templates() -> list[dict[str, Any]]:
    """Return built-in agent templates (delegates to agents.templates module)."""
    from agents.templates import list_templates as _list

    return _list()
