# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for custom agent definitions (Phase 3 — extensibility)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentDefinition(BaseModel):
    """Full representation of a user-defined custom agent."""

    model_config = ConfigDict(extra="allow")

    agent_id: str = Field(..., description="Unique identifier (UUID)")
    name: str = Field(..., min_length=1, max_length=128, description="Human-readable agent name")
    description: str = Field(default="", max_length=1024, description="What this agent does")
    system_prompt: str = Field(
        default="",
        max_length=8192,
        description="System prompt injected at the start of every conversation",
    )
    tools: list[str] = Field(
        default_factory=list,
        description="List of MCP tool names this agent can use (empty = all)",
    )
    domains: list[str] = Field(
        default_factory=list,
        description="KB domains this agent searches (empty = all)",
    )
    rag_mode: str = Field(
        default="smart",
        description="Retrieval mode: smart | simple | off",
    )
    model_override: str | None = Field(
        default=None,
        description="Override the default LLM model for this agent (e.g. 'openai/gpt-4o')",
    )
    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, description="LLM temperature"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value metadata",
    )
    template_id: str | None = Field(
        default=None,
        description="Template this agent was created from, if any",
    )
    created_at: str = Field(default="", description="ISO 8601 creation timestamp")
    updated_at: str = Field(default="", description="ISO 8601 last-update timestamp")


class AgentCreateRequest(BaseModel):
    """Request body for creating a custom agent."""

    name: str = Field(..., min_length=1, max_length=128, description="Agent name")
    description: str = Field(default="", max_length=1024, description="Agent description")
    system_prompt: str = Field(
        default="",
        max_length=8192,
        description="System prompt for the agent",
    )
    tools: list[str] = Field(default_factory=list, description="Allowed tool names")
    domains: list[str] = Field(default_factory=list, description="KB domains to search")
    rag_mode: str = Field(default="smart", description="Retrieval mode: smart | simple | off")
    model_override: str | None = Field(default=None, description="LLM model override")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="LLM temperature")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata")


class AgentUpdateRequest(BaseModel):
    """Partial update for a custom agent (PATCH semantics — all fields optional)."""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    system_prompt: str | None = Field(default=None, max_length=8192)
    tools: list[str] | None = Field(default=None)
    domains: list[str] | None = Field(default=None)
    rag_mode: str | None = Field(default=None)
    model_override: str | None = Field(default=None)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    metadata: dict[str, Any] | None = Field(default=None)


class AgentListResponse(BaseModel):
    """Paginated list of custom agents."""

    agents: list[AgentDefinition] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)


class AgentQueryRequest(BaseModel):
    """Request body for querying through a custom agent."""

    query: str = Field(..., min_length=1, max_length=4096, description="User query text")
    conversation_id: str | None = Field(
        default=None, description="Conversation ID for context continuity"
    )
    stream: bool = Field(default=False, description="Enable streaming response")
