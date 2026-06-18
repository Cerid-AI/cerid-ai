# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reader adapter recipes.

Providers: readwise, pocket, instapaper, raindrop, telegram.

These services either push webhooks (Readwise highlights, Telegram
forwards) or expose polled APIs (Pocket, Instapaper, Raindrop). The
adapter recipes here normalize the *push* shape; the polled worker
applies the same recipe to each page of the polled response.
"""
from __future__ import annotations

from typing import Any

from core.ingest.adapters.registry import (
    AdapterRecipe,
    CanonicalArtifact,
    register_recipe,
)

_KIND = "external_adapter"


def _readwise_recipe(payload: dict[str, Any], _src_config: dict[str, Any]) -> list[CanonicalArtifact]:
    """Readwise highlight payload — list of ``highlights`` per book."""
    out: list[CanonicalArtifact] = []
    for book in payload.get("books") or [payload]:
        title = book.get("title") or book.get("source_title") or "Highlight"
        author = book.get("author") or ""
        for hl in book.get("highlights") or [book]:
            text = (hl.get("text") or hl.get("note") or "").strip()
            if not text:
                continue
            out.append(
                CanonicalArtifact(
                    title=f"{title} — {author}: {text[:60]}" if author else f"{title}: {text[:60]}",
                    content=text,
                    url=hl.get("url") or book.get("source_url"),
                    timestamp=hl.get("highlighted_at") or hl.get("updated"),
                    provider="readwise",
                    raw=hl,
                )
            )
    return out


def _pocket_recipe(payload: dict[str, Any], _src_config: dict[str, Any]) -> list[CanonicalArtifact]:
    """Pocket ``list`` shape, keyed by item_id."""
    out: list[CanonicalArtifact] = []
    items = payload.get("list") or {}
    if isinstance(items, dict):
        items = items.values()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("resolved_url") or item.get("given_url") or ""
        title = item.get("resolved_title") or item.get("given_title") or url
        excerpt = item.get("excerpt") or ""
        if not (title or excerpt):
            continue
        out.append(
            CanonicalArtifact(
                title=title[:80],
                content=excerpt,
                url=url,
                timestamp=item.get("time_added"),
                provider="pocket",
                raw=item,
            )
        )
    return out


def _instapaper_recipe(payload: dict[str, Any], _src_config: dict[str, Any]) -> list[CanonicalArtifact]:
    """Instapaper export JSON shape."""
    out: list[CanonicalArtifact] = []
    for item in payload.get("items") or [payload]:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or ""
        if not title:
            continue
        out.append(
            CanonicalArtifact(
                title=title[:80],
                content=item.get("description") or "",
                url=item.get("url"),
                timestamp=item.get("time"),
                provider="instapaper",
                raw=item,
            )
        )
    return out


def _raindrop_recipe(payload: dict[str, Any], _src_config: dict[str, Any]) -> list[CanonicalArtifact]:
    """Raindrop.io webhook / API list shape."""
    out: list[CanonicalArtifact] = []
    for item in payload.get("items") or payload.get("raindrops") or [payload]:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or ""
        if not title:
            continue
        out.append(
            CanonicalArtifact(
                title=title[:80],
                content=item.get("excerpt") or item.get("note") or "",
                url=item.get("link"),
                timestamp=item.get("lastUpdate") or item.get("created"),
                provider="raindrop",
                raw=item,
            )
        )
    return out


def _telegram_recipe(payload: dict[str, Any], _src_config: dict[str, Any]) -> list[CanonicalArtifact]:
    """Telegram bot update shape (forwarded messages, link previews)."""
    msg = payload.get("message") or payload.get("channel_post") or {}
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not text:
        return []
    fwd = msg.get("forward_from_chat", {}) or msg.get("forward_from", {})
    title = fwd.get("title") or fwd.get("username") or msg.get("chat", {}).get("title", "Telegram")
    return [
        CanonicalArtifact(
            title=f"{title}: {text[:60]}",
            content=text,
            url=None,
            timestamp=msg.get("date") and str(msg["date"]),
            provider="telegram",
            raw=payload,
        )
    ]


def register() -> None:
    register_recipe(AdapterRecipe(kind=_KIND, provider="readwise", fn=_readwise_recipe))
    register_recipe(AdapterRecipe(kind=_KIND, provider="pocket", fn=_pocket_recipe))
    register_recipe(AdapterRecipe(kind=_KIND, provider="instapaper", fn=_instapaper_recipe))
    register_recipe(AdapterRecipe(kind=_KIND, provider="raindrop", fn=_raindrop_recipe))
    register_recipe(AdapterRecipe(kind=_KIND, provider="telegram", fn=_telegram_recipe))
