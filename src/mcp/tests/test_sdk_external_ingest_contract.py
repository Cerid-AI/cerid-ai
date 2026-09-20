# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The published /sdk/v1/ingest/external contract must match the server (H008).

F012 replaced one false published claim with another: the shipped OpenAPI
description told external integrators that mixing a fanned-out ``content``
path with a plain ``source_uri`` "is a 422". ``ingest_external`` catches
MappingError deliberately (app/services/external_ingest.py) and answers 200
with the failure in the ``errors`` array, so no 422 exists on this path.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_MIXED_FANOUT = {
    "source_type": "readwise",
    "payload": {
        "highlights": [{"text": "one"}, {"text": "two"}],
        "url": "https://example.com/a",
    },
    "field_mappings": {"content": "highlights[].text", "source_uri": "url"},
}


@pytest.fixture
def client():
    from app.routers import sdk

    app = FastAPI()
    app.include_router(sdk.router)
    return TestClient(app)


def _route():
    from app.routers import sdk

    return next(
        r for r in sdk.router.routes
        if getattr(r, "path", None) == "/sdk/v1/ingest/external"
    )


def test_a_mapping_failure_is_a_200_with_a_structured_error(client):
    resp = client.post("/sdk/v1/ingest/external", json=_MIXED_FANOUT)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] == 0
    assert body["errors"][0]["phase"] == "mapping"
    assert "Fan-out mismatch" in body["errors"][0]["error"]


def test_the_description_does_not_promise_a_status_the_route_cannot_return(client):
    """The prose is the contract external integrators read — pin it."""
    returned = client.post("/sdk/v1/ingest/external", json=_MIXED_FANOUT).status_code
    description = _route().description

    assert "422" not in description, (
        "the description still claims 422 for a mapping failure; the route "
        f"returned {returned}"
    )
    assert "errors" in description, (
        "the description must say where a mapping failure is reported"
    )
