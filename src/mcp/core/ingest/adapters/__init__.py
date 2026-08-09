# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Adapter recipes — provider-aware webhook payload normalizers.

Each recipe knows how to turn an inbound JSON payload from a specific
third-party provider (Slack, Discord, GitHub, Linear, Sentry, Stripe,
Readwise, Pocket, Telegram, …) into a canonical artifact shape that
the ingest worker can consume:

    {
      "title":     str,
      "content":   str,
      "url":       str | None,
      "timestamp": iso8601 | None,
      "provider":  str,
      "raw":       dict   # the original payload, kept for debugging
    }

Recipes live in :mod:`core.ingest.adapters` so they obey the
``core → app`` import rule. The webhook receiver in
:mod:`app.routers.sdk` looks up the recipe by
``source.config.provider`` and applies it before enqueuing.

Adding a new provider = new module here + one ``register_recipe``
call. No router changes required.
"""
from __future__ import annotations

from core.ingest.adapters import chat as _chat
from core.ingest.adapters import dev_events as _dev_events
from core.ingest.adapters import reader as _reader
from core.ingest.adapters.registry import (
    AdapterRecipe,
    CanonicalArtifact,
    get_recipe,
    iter_recipes,
    register_recipe,
)

# Side-effect-register every recipe at package load.
_chat.register()
_dev_events.register()
_reader.register()

__all__ = [
    "AdapterRecipe",
    "CanonicalArtifact",
    "get_recipe",
    "iter_recipes",
    "register_recipe",
]
