# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Adapter recipe registry — keyed by ``(kind, provider)``."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterator

logger = logging.getLogger("ai-companion.adapters.registry")


@dataclass(frozen=True)
class CanonicalArtifact:
    """Normalized shape every adapter produces. The ingest worker
    converts this into a Chroma + Neo4j artifact in one call.
    """

    title: str
    content: str
    url: str | None = None
    timestamp: str | None = None
    provider: str = ""
    raw: dict[str, Any] | None = None


# A recipe takes the raw payload and the source's config (which may
# carry per-source field overrides) and returns either a single
# CanonicalArtifact or a list (e.g., a Slack history push may
# contain many messages). Returning an empty list means "ignored —
# don't enqueue."
RecipeFn = Callable[
    [dict[str, Any], dict[str, Any]],
    list[CanonicalArtifact],
]


@dataclass(frozen=True)
class AdapterRecipe:
    """A registered (kind, provider) recipe."""

    kind: str  # one of "chat_capture", "dev_events", "external_adapter" etc.
    provider: str  # provider sub-key: "slack", "github", "readwise", ...
    fn: RecipeFn
    requires_signature: bool = False  # provider mandates HMAC


_REGISTRY: dict[tuple[str, str], AdapterRecipe] = {}

# Provider → canonical kind index. Built incrementally as recipes
# register. Lets the webhook receiver resolve a recipe even when the
# inbound (:Source) is kind=webhook (the security/routing boundary
# for inbound traffic) — we just look up by the provider name.
_PROVIDER_INDEX: dict[str, AdapterRecipe] = {}


def register_recipe(recipe: AdapterRecipe) -> None:
    """Register a recipe. Re-registration replaces the previous instance."""
    key = (recipe.kind, recipe.provider)
    if key in _REGISTRY and _REGISTRY[key] is not recipe:
        logger.debug("Replacing adapter recipe for %s/%s", *key)
    _REGISTRY[key] = recipe
    _PROVIDER_INDEX[recipe.provider] = recipe


def get_recipe(kind: str, provider: str) -> AdapterRecipe | None:
    """Look up a recipe by (kind, provider). When ``kind`` is the
    inbound-routing kind (``webhook``) the canonical kind embedded
    in the recipe takes precedence via the provider index — caller
    doesn't need to know the destination kind in advance.
    """
    if kind == "webhook":
        return _PROVIDER_INDEX.get(provider)
    return _REGISTRY.get((kind, provider))


def iter_recipes() -> Iterator[AdapterRecipe]:
    return iter(_REGISTRY.values())


def reset_for_tests() -> None:
    _REGISTRY.clear()
    _PROVIDER_INDEX.clear()
