# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Connector implementations registered at process boot.

Importing this package side-effect-registers each kind's connector
into ``core.ingest.sources.registry``. The router imports it
exactly once via ``app.main``.
"""
from __future__ import annotations

from core.ingest.sources.connectors import apple_mail as _apple_mail
from core.ingest.sources.connectors import bookmarks as _bookmarks
from core.ingest.sources.connectors import clipboard as _clipboard
from core.ingest.sources.connectors import rss as _rss
from core.ingest.sources.connectors import url_watch as _url_watch
from core.ingest.sources.registry import register_connector

# apple_reminders deliberately has NO connector here: it ingests through the
# desktop app's main process (packages/desktop/src/main/connectors/
# apple_reminders.ts), like Notes / Calendar / Photos / iMessage. The former
# container-side connector resolved `ceridreminders` with shutil.which inside
# the Linux MCP container, where no helper is mounted — it could never connect.

register_connector(_rss.RssConnector())
register_connector(_url_watch.UrlWatchConnector())
register_connector(_bookmarks.BookmarksConnector())
register_connector(_clipboard.ClipboardConnector())
register_connector(_apple_mail.AppleMailConnector())

__all__ = [
    "_apple_mail",
    "_bookmarks",
    "_clipboard",
    "_rss",
    "_url_watch",
]
