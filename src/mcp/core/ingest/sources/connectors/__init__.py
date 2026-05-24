# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Connector implementations registered at process boot.

Importing this package side-effect-registers each kind's connector
into ``core.ingest.sources.registry``. The router imports it
exactly once via ``app.main``.
"""
from __future__ import annotations

from core.ingest.sources.connectors import rss as _rss
from core.ingest.sources.connectors import url_watch as _url_watch
from core.ingest.sources.registry import register_connector

register_connector(_rss.RssConnector())
register_connector(_url_watch.UrlWatchConnector())

__all__ = ["_rss", "_url_watch"]
