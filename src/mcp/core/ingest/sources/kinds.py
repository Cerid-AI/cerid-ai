# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The 21 valid Source kinds + their tier + family mappings.

This is the single source of truth referenced by:

* :mod:`app.db.neo4j.sources` — validates ``kind`` on create
* :mod:`core.ingest.sources.registry` — connector lookup
* :mod:`app.routers.sources` — FE-facing source-kind enumeration
* ``src/web/src/components/sources/`` — FE imports the same names
  via the SDK package's TypeScript types

Adding a new kind = (a) extend the literals here, (b) add the
connector module, (c) re-export the FE TS type. Three files
touched; no other migration required.
"""
from __future__ import annotations

from typing import Final, Literal

# ---------------------------------------------------------------------------
# Type aliases — Literal[...] gives mypy + Pydantic enum-grade validation
# ---------------------------------------------------------------------------

SourceFamily = Literal[
    "files", "feeds", "chat", "mail", "calendar", "media", "webhook", "adapter", "pack",
]

SourceKind = Literal[
    # Core (10)
    "folder", "bookmarks", "rss", "url_watch", "webhook", "chat_capture",
    "dev_events", "clipboard", "voice_note", "external_adapter",
    # Knowledge packs (Core)
    "knowledge_pack",
    # Pro (11) — gated by FEATURE_FLAGS["pro_*"]
    "gmail", "outlook", "google_calendar", "outlook_calendar", "meeting_audio",
    "apple_notes", "apple_mail", "imessage", "apple_calendar", "apple_photos",
    "apple_reminders",
]

# ---------------------------------------------------------------------------
# Maps
# ---------------------------------------------------------------------------

# Mapping from kind → family. Used by the radial-FAB UI to group
# connectors and by the Constellation node-color palette.
KIND_FAMILY: Final[dict[SourceKind, SourceFamily]] = {
    # files
    "folder": "files",
    "bookmarks": "files",
    "apple_notes": "files",
    # feeds
    "rss": "feeds",
    "url_watch": "feeds",
    # chat
    "imessage": "chat",
    # mail
    "gmail": "mail",
    "outlook": "mail",
    "apple_mail": "mail",
    # calendar
    "google_calendar": "calendar",
    "outlook_calendar": "calendar",
    "apple_calendar": "calendar",
    "apple_reminders": "calendar",
    # media
    "clipboard": "media",
    "voice_note": "media",
    "meeting_audio": "media",
    "apple_photos": "media",
    # webhook
    "webhook": "webhook",
    "chat_capture": "webhook",
    "dev_events": "webhook",
    # adapter
    "external_adapter": "adapter",
    # pack
    "knowledge_pack": "pack",
}

# Mapping from kind → tier. Read at startup; the FE shows the Pro
# badge based on this table, and the connector instantiation path
# enforces it via ``app.config.features.is_feature_enabled``.
KIND_TIER: Final[dict[SourceKind, Literal["core", "pro"]]] = {
    # Core
    "folder": "core",
    "bookmarks": "core",
    "rss": "core",
    "url_watch": "core",
    "webhook": "core",
    "chat_capture": "core",
    "dev_events": "core",
    "clipboard": "core",
    "voice_note": "core",
    "external_adapter": "core",
    "knowledge_pack": "core",
    # Pro
    "gmail": "pro",
    "outlook": "pro",
    "google_calendar": "pro",
    "outlook_calendar": "pro",
    "meeting_audio": "pro",
    "apple_notes": "pro",
    "apple_mail": "pro",
    "imessage": "pro",
    "apple_calendar": "pro",
    "apple_photos": "pro",
    "apple_reminders": "pro",
}

# Tuple constants — useful for runtime validation and tests.
SOURCE_KINDS: Final[tuple[SourceKind, ...]] = tuple(KIND_FAMILY.keys())
CORE_KINDS: Final[tuple[SourceKind, ...]] = tuple(
    k for k, tier in KIND_TIER.items() if tier == "core"
)
PRO_KINDS: Final[tuple[SourceKind, ...]] = tuple(
    k for k, tier in KIND_TIER.items() if tier == "pro"
)

# Sanity asserts — drift between maps fails fast at import time.
assert set(KIND_FAMILY.keys()) == set(KIND_TIER.keys()), (
    "KIND_FAMILY and KIND_TIER must cover the same kinds"
)
assert len(SOURCE_KINDS) == 22, (
    f"Expected 22 source kinds (11 Core + 11 Pro), got {len(SOURCE_KINDS)}"
)
assert len(CORE_KINDS) == 11, f"Expected 11 Core kinds, got {len(CORE_KINDS)}"
assert len(PRO_KINDS) == 11, f"Expected 11 Pro kinds, got {len(PRO_KINDS)}"
