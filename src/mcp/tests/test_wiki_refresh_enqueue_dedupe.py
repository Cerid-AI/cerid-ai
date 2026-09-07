# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Live and sweep refreshes for the same entity must collapse onto one job.

``origin`` rides in the ``WikiRefreshJob`` payload so the job can exempt
sweep-originated runs from the live deferral, but it is bookkeeping, not
identity: a queued sweep refresh for slug S and a live refresh for S do the
same work. The queue dedupes on the identity fields only.
"""
from __future__ import annotations

from unittest.mock import patch

import fakeredis
import pytest

from app.processor.subscribers.wiki_refresh import enqueue_refresh

_SLUG = "ada-lovelace"
_LOW_QUEUE = "cerid:proc:queue:low"


@pytest.fixture()
def redis_client():
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    with (
        patch("app.deps.get_redis", return_value=client),
        patch("app.deps.get_neo4j", return_value=None),
    ):
        yield client


def test_live_enqueue_collapses_onto_queued_sweep_job(redis_client):
    assert enqueue_refresh(_SLUG, force=True, origin="sweep") is True
    assert enqueue_refresh(_SLUG, force=True, origin="live") is False
    assert redis_client.llen(_LOW_QUEUE) == 1


def test_sweep_enqueue_collapses_onto_queued_live_job(redis_client):
    assert enqueue_refresh(_SLUG, force=True, origin="live") is True
    assert enqueue_refresh(_SLUG, force=True, origin="sweep") is False
    assert redis_client.llen(_LOW_QUEUE) == 1


def test_other_slug_still_enqueues(redis_client):
    assert enqueue_refresh(_SLUG, force=True, origin="sweep") is True
    assert enqueue_refresh("alan-turing", force=True, origin="live") is True
    assert redis_client.llen(_LOW_QUEUE) == 2
