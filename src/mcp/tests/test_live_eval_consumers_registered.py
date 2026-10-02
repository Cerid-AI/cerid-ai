# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The live eval harnesses query with a registered consumer that can see their fixtures.

An unregistered X-Client-ID is scoped to the "general" domain. The retrieval
eval sent a random one, so its fixtures (coding, notes, projects) were never
retrievable, and the nightly gate failed as if indexing were lagging.
"""
from __future__ import annotations

import pytest

from app.services.request_policy import resolve_consumer
from config.settings import CONSUMER_REGISTRY
from tests.eval import _live_eval_common, verification_verdict_eval


@pytest.mark.parametrize(
    "client_id",
    [_live_eval_common.CLIENT_ID, verification_verdict_eval.CLIENT_ID],
)
def test_harness_client_is_registered_with_full_scope(client_id):
    assert client_id in CONSUMER_REGISTRY
    consumer = resolve_consumer(client_id)
    assert consumer is not CONSUMER_REGISTRY["_default"]
    assert consumer["allowed_domains"] is None
    assert consumer["strict_domains"] is False


def test_the_live_client_sends_the_registered_id():
    client = _live_eval_common.make_client()
    try:
        assert client.headers["X-Client-ID"] == _live_eval_common.CLIENT_ID
    finally:
        client.close()
