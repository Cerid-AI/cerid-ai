# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Dev-events adapter recipes.

Providers: github, linear, sentry, stripe.

GitHub mandates HMAC verification (``X-Hub-Signature-256``); the
webhook receiver already routes per-source HMAC checks via the
source's ``config.hmac_secret``, so per-provider extras here are
purely about payload shape.
"""
from __future__ import annotations

from typing import Any

from core.ingest.adapters.registry import (
    AdapterRecipe,
    CanonicalArtifact,
    register_recipe,
)

_KIND = "dev_events"


def _github_recipe(payload: dict[str, Any], _src_config: dict[str, Any]) -> list[CanonicalArtifact]:
    """GitHub webhook event. We use the ``action`` + top-level keys
    (issue / pull_request / push / release) to derive title + content."""
    if "pull_request" in payload:
        pr = payload["pull_request"]
        action = payload.get("action", "updated")
        return [
            CanonicalArtifact(
                title=f"PR {action}: #{pr.get('number')} {pr.get('title', '')[:60]}",
                content=pr.get("body", "") or "",
                url=pr.get("html_url"),
                timestamp=pr.get("updated_at"),
                provider="github",
                raw=payload,
            )
        ]
    if "issue" in payload:
        iss = payload["issue"]
        action = payload.get("action", "updated")
        return [
            CanonicalArtifact(
                title=f"Issue {action}: #{iss.get('number')} {iss.get('title', '')[:60]}",
                content=iss.get("body", "") or "",
                url=iss.get("html_url"),
                timestamp=iss.get("updated_at"),
                provider="github",
                raw=payload,
            )
        ]
    if "release" in payload:
        rel = payload["release"]
        return [
            CanonicalArtifact(
                title=f"Release {rel.get('tag_name')}: {rel.get('name', '')[:60]}",
                content=rel.get("body", "") or "",
                url=rel.get("html_url"),
                timestamp=rel.get("published_at"),
                provider="github",
                raw=payload,
            )
        ]
    # Push events: skip — too noisy unless explicitly requested
    return []


def _linear_recipe(payload: dict[str, Any], _src_config: dict[str, Any]) -> list[CanonicalArtifact]:
    """Linear webhook. ``data`` holds the issue / project / comment."""
    data = payload.get("data") or {}
    action = payload.get("action", "updated")
    title = data.get("title") or data.get("body") or ""
    if not title:
        return []
    return [
        CanonicalArtifact(
            title=f"Linear {action}: {title[:60]}",
            content=data.get("description") or data.get("body") or "",
            url=data.get("url"),
            timestamp=data.get("updatedAt") or payload.get("createdAt"),
            provider="linear",
            raw=payload,
        )
    ]


def _sentry_recipe(payload: dict[str, Any], _src_config: dict[str, Any]) -> list[CanonicalArtifact]:
    """Sentry issue alert / event hook shape."""
    event = payload.get("event") or payload.get("data", {}).get("issue") or payload
    title = event.get("title") or event.get("message") or ""
    if not title:
        return []
    return [
        CanonicalArtifact(
            title=f"Sentry: {title[:80]}",
            content=event.get("message", "") or "",
            url=event.get("url") or event.get("web_url"),
            timestamp=event.get("datetime") or event.get("lastSeen"),
            provider="sentry",
            raw=payload,
        )
    ]


def _stripe_recipe(payload: dict[str, Any], _src_config: dict[str, Any]) -> list[CanonicalArtifact]:
    """Stripe ``Event`` envelope."""
    typ = payload.get("type", "event")
    data = (payload.get("data") or {}).get("object") or {}
    descr = data.get("description") or data.get("id") or typ
    return [
        CanonicalArtifact(
            title=f"Stripe {typ}",
            content=f"{descr}",
            url=None,
            timestamp=payload.get("created") and str(payload["created"]),
            provider="stripe",
            raw=payload,
        )
    ]


def register() -> None:
    register_recipe(AdapterRecipe(kind=_KIND, provider="github", fn=_github_recipe, requires_signature=True))
    register_recipe(AdapterRecipe(kind=_KIND, provider="linear", fn=_linear_recipe))
    register_recipe(AdapterRecipe(kind=_KIND, provider="sentry", fn=_sentry_recipe))
    register_recipe(AdapterRecipe(kind=_KIND, provider="stripe", fn=_stripe_recipe, requires_signature=True))
