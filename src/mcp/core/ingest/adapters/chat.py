# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Chat-capture adapter recipes — B2.7.

Providers: slack, discord, teams, matrix.
"""
from __future__ import annotations

from typing import Any

from core.ingest.adapters.registry import (
    AdapterRecipe,
    CanonicalArtifact,
    register_recipe,
)

_KIND = "chat_capture"


def _slack_recipe(payload: dict[str, Any], _src_config: dict[str, Any]) -> list[CanonicalArtifact]:
    """Slack event-API ``message`` shape:

        { "event": { "type": "message", "user": "...", "text": "...",
                     "channel": "...", "ts": "1700000000.000100" }, ... }
    """
    event = payload.get("event") or payload
    if event.get("type") not in ("message", None):
        return []
    text = (event.get("text") or "").strip()
    if not text:
        return []
    channel = event.get("channel", "")
    ts = event.get("ts")
    return [
        CanonicalArtifact(
            title=f"#{channel}: {text[:60]}" if channel else text[:60],
            content=text,
            url=None,
            timestamp=ts,
            provider="slack",
            raw=payload,
        )
    ]


def _discord_recipe(payload: dict[str, Any], _src_config: dict[str, Any]) -> list[CanonicalArtifact]:
    """Discord webhook ``message`` shape (Webhook API + bot intents)."""
    content = (payload.get("content") or "").strip()
    if not content:
        return []
    author = payload.get("author") or {}
    return [
        CanonicalArtifact(
            title=f"{author.get('username', '?')}: {content[:60]}",
            content=content,
            url=None,
            timestamp=payload.get("timestamp"),
            provider="discord",
            raw=payload,
        )
    ]


def _teams_recipe(payload: dict[str, Any], _src_config: dict[str, Any]) -> list[CanonicalArtifact]:
    """Microsoft Teams Adaptive Card / change-notification shape."""
    text = (
        payload.get("text")
        or payload.get("body", {}).get("content")
        or ""
    ).strip()
    if not text:
        return []
    from_user = payload.get("from", {}).get("user", {}).get("displayName", "")
    return [
        CanonicalArtifact(
            title=f"{from_user}: {text[:60]}" if from_user else text[:60],
            content=text,
            timestamp=payload.get("createdDateTime"),
            provider="teams",
            raw=payload,
        )
    ]


def _matrix_recipe(payload: dict[str, Any], _src_config: dict[str, Any]) -> list[CanonicalArtifact]:
    """Matrix ``m.room.message`` event shape."""
    content_obj = payload.get("content") or {}
    body = (content_obj.get("body") or "").strip()
    if not body:
        return []
    return [
        CanonicalArtifact(
            title=body[:60],
            content=body,
            timestamp=payload.get("origin_server_ts"),
            provider="matrix",
            raw=payload,
        )
    ]


def register() -> None:
    register_recipe(AdapterRecipe(kind=_KIND, provider="slack", fn=_slack_recipe))
    register_recipe(AdapterRecipe(kind=_KIND, provider="discord", fn=_discord_recipe))
    register_recipe(AdapterRecipe(kind=_KIND, provider="teams", fn=_teams_recipe))
    register_recipe(AdapterRecipe(kind=_KIND, provider="matrix", fn=_matrix_recipe))
