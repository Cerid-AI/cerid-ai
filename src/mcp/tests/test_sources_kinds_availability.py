# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the availability capability flag on source kinds.

The wizard gates kinds that have no working ingestion path; availability is
derived from the connector registry + OAuth set + the webhook special-case.
"""
from __future__ import annotations

from app.routers.connectors import oauth_connector_kinds
from app.routers.sources import _kind_availability


def test_registered_connector_is_available():
    # rss has a registered SourceConnector
    assert _kind_availability("rss", oauth_connector_kinds()) == "available"
    assert _kind_availability("url_watch", oauth_connector_kinds()) == "available"


def test_webhook_is_available_without_connector():
    assert _kind_availability("webhook", oauth_connector_kinds()) == "available"


def test_oauth_kind_is_oauth():
    oauth = oauth_connector_kinds()
    assert "gmail" in oauth  # guards the fixture
    assert _kind_availability("gmail", oauth) == "oauth"


def test_unimplemented_kind_is_coming_soon():
    # folder is declared in SOURCE_KINDS but has no connector and no OAuth path
    assert _kind_availability("folder", oauth_connector_kinds()) == "coming_soon"


def test_every_source_kind_classified():
    from core.ingest.sources.kinds import SOURCE_KINDS

    oauth = oauth_connector_kinds()
    valid = {"available", "oauth", "coming_soon"}
    for k in SOURCE_KINDS:
        assert _kind_availability(k, oauth) in valid
