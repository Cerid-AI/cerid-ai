# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The published SDK spec must declare the headers the server actually requires.

``docs/SDK_GUIDE.md`` tells consumers to generate clients from this spec. A
spec with no ``securitySchemes`` produces a client that sends neither
``X-Client-ID`` nor ``X-API-Key``: it lands in the unregistered rate bucket
and 401s outright on any server with ``CERID_API_KEY`` set, with nothing in
the contract to hint that headers were needed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.routers.sdk_openapi import _build_sdk_spec

SPEC_PATH = Path(__file__).resolve().parents[3] / "docs" / "openapi-sdk-v1.json"

EXPECTED_SCHEMES = {
    "client_id": {"type": "apiKey", "in": "header", "name": "X-Client-ID"},
    "api_key": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
}


@pytest.fixture(params=["built", "committed"])
def spec(request: pytest.FixtureRequest) -> dict:
    """Both the spec the server serves and the artifact consumers download."""
    if request.param == "built":
        return _build_sdk_spec()
    return json.loads(SPEC_PATH.read_text())


def test_spec_declares_both_auth_headers(spec: dict) -> None:
    schemes = spec.get("components", {}).get("securitySchemes")
    assert schemes is not None, "no components.securitySchemes — generated clients are unauthenticated"
    for name, definition in EXPECTED_SCHEMES.items():
        declared = schemes.get(name)
        assert declared is not None, f"{name} scheme missing"
        identity = {k: declared.get(k) for k in definition}
        assert identity == definition, f"{name} scheme is {identity!r}, expected {definition!r}"


def test_spec_applies_client_id_by_default(spec: dict) -> None:
    """X-Client-ID is required on every call; X-API-Key only when the server
    sets CERID_API_KEY, so it is offered as an alternative rather than a
    second mandatory header."""
    security = spec.get("security")
    assert security, "no top-level security entry — codegen emits no auth parameters"
    assert {"client_id": []} in security
