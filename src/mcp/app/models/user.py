# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""User and Tenant domain models for multi-user auth."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Tenant(BaseModel):
    """A tenant groups users who share access to the same knowledge base."""

    id: str = Field(..., description="UUID tenant identifier")
    name: str = Field(..., description="Human-readable tenant name")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class User(BaseModel):
    """Registered user within a tenant."""

    id: str = Field(..., description="UUID user identifier")
    email: str = Field(..., description="Login email address")
    hashed_password: str = Field(..., description="bcrypt hash")
    display_name: str = Field("", description="User display name")
    role: str = Field("member", description="admin or member")
    tenant_id: str = Field(..., description="Owning tenant UUID")
    openrouter_api_key_encrypted: str | None = Field(
        None, description="Fernet-encrypted OpenRouter API key"
    )
    usage_queries: int = Field(0, description="Running query count for metering")
    usage_ingestions: int = Field(0, description="Running ingestion count")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_login: datetime | None = None


class UserPublic(BaseModel):
    """Safe user representation (no password hash, no encrypted keys)."""

    id: str
    email: str
    display_name: str
    role: str
    tenant_id: str
    has_api_key: bool = False
    usage_queries: int = 0
    usage_ingestions: int = 0
    created_at: datetime
    last_login: datetime | None = None
