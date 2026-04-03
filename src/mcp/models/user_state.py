# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed response models for the ``/user-state`` endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "UserStateSummary",
    "ConversationSaveResponse",
    "ConversationBulkSaveResponse",
]


class _UserStateBase(BaseModel):
    """Base for all user-state response models — allows extra fields for forward compat."""

    model_config = ConfigDict(extra="allow")


class UserStateSummary(_UserStateBase):
    """Response from ``GET /user-state`` — settings, preferences, and conversation IDs."""

    settings: dict[str, Any] = Field(default_factory=dict, description="User settings")
    preferences: dict[str, Any] = Field(default_factory=dict, description="UI preferences")
    conversation_ids: list[str | None] = Field(default_factory=list, description="Known conversation IDs")


class ConversationSaveResponse(_UserStateBase):
    """Response from ``POST /user-state/conversations``."""

    saved: str = Field(default="", description="ID of the saved conversation")


class ConversationBulkSaveResponse(_UserStateBase):
    """Response from ``POST /user-state/conversations/bulk``."""

    saved: int = Field(default=0, ge=0, description="Number of conversations saved")
