# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract tests: pin the Python SDK against docs/openapi-sdk-v1.json.

``docs/openapi-sdk-v1.json`` is generated from the live FastAPI routes by
``scripts/gen_sdk_openapi.py`` and is the authoritative ``/sdk/v1/*``
contract (the ``sdk-openapi-drift`` CI gate keeps it byte-for-byte in sync
with the server). These tests are the other half of that pin: for every
wrapped SDK method they assert

  1. the JSON body the method actually sends over the wire validates
     against the spec's ``requestBody`` schema for that operation, and
  2. the SDK's response model declares every field the spec's ``200``
     schema marks ``required``.

A contract test that only exercises a hand-written fixture proves nothing
about the real client code path — every assertion here drives the actual
``cerid.CeridClient`` resource methods with a mocked transport and inspects
what they really produced.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import httpx
import pytest
from cerid import AsyncCeridClient, CeridClient
from cerid.__version__ import SDK_PROTOCOL_VERSION
from cerid.models import (
    HallucinationResponse,
    HealthResponse,
    IngestExternalResponse,
    LLMCompleteResponse,
    MemoryExtractJobStatus,
    MemoryExtractResponse,
    PluginListResponse,
    QueryResponse,
    SearchResponse,
    SettingsResponse,
    TaxonomyResponse,
)
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[4]
SPEC_PATH = REPO_ROOT / "docs" / "openapi-sdk-v1.json"
SPEC: dict[str, Any] = json.loads(SPEC_PATH.read_text())
SCHEMAS: dict[str, Any] = SPEC["components"]["schemas"]


# ---------------------------------------------------------------------------
# Spec helpers: $ref resolution, schema lookup, minimal-instance synthesis.
# ---------------------------------------------------------------------------


def _deref(schema: dict[str, Any], _seen: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Fully inline every ``$ref`` so validators/generators see a flat schema."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name in _seen:
            return {}  # cycle guard; the spec has none today
        return _deref(SCHEMAS[name], _seen | {name})
    out = dict(schema)
    if "anyOf" in out:
        out["anyOf"] = [_deref(s, _seen) for s in out["anyOf"]]
    if "properties" in out:
        out["properties"] = {k: _deref(v, _seen) for k, v in out["properties"].items()}
    if "items" in out:
        out["items"] = _deref(out["items"], _seen)
    return out


def _request_schema(path: str, method: str) -> dict[str, Any]:
    body = SPEC["paths"][path][method]["requestBody"]["content"]["application/json"]["schema"]
    return _deref(body)


def _response_schema(path: str, method: str, status: str = "200") -> dict[str, Any]:
    resp = SPEC["paths"][path][method]["responses"][status]["content"]["application/json"]["schema"]
    return _deref(resp)


def _mock_response(status_code: int, json_body: Any) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_body,
        request=httpx.Request("POST", "http://test"),
    )


def _capture_post(client: CeridClient, response_fixture: dict[str, Any]) -> MagicMock:
    mock = MagicMock(return_value=_mock_response(200, response_fixture))
    client._http.post = mock
    return mock


def _capture_get(client: CeridClient, response_fixture: dict[str, Any]) -> MagicMock:
    mock = MagicMock(return_value=_mock_response(200, response_fixture))
    client._http.get = mock
    return mock


def _assert_response_model_covers_required(response_model: type, response_schema: dict[str, Any], label: str) -> None:
    required = set(response_schema.get("required", []))
    declared = set(response_model.model_fields)
    missing = required - declared
    assert not missing, (
        f"{label}: spec requires {sorted(missing)} on the response but "
        f"{response_model.__name__} doesn't declare {sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# Endpoint table: one row per wrapped `/sdk/v1/*` POST method. ``invoke``
# calls the real SDK method with representative arguments against a client
# whose transport is mocked above; ``response_model`` is the Pydantic model
# whose declared fields must be a superset of the spec's `required` list;
# ``response_fixture`` is a realistic 200 payload, itself validated against
# the spec's response schema before it's used to mock the transport (so a
# fixture that drifted from the spec fails loudly instead of masking a real
# response-shape bug behind a bad test double).
# ---------------------------------------------------------------------------
PostCase = tuple[str, str, str, Callable[[CeridClient], Any], type, dict[str, Any]]

POST_CASES: list[PostCase] = [
    (
        "kb.query",
        "/sdk/v1/query",
        "post",
        lambda c: c.kb.query("test query", domains=["general"], top_k=5),
        QueryResponse,
        {"context": "assembled context", "sources": [], "confidence": 0.8},
    ),
    (
        "kb.search",
        "/sdk/v1/search",
        "post",
        lambda c: c.kb.search("test query", domain="general", top_k=3),
        SearchResponse,
        {"results": [], "total_results": 0, "confidence": 0.0},
    ),
    (
        "kb.ingest_external",
        "/sdk/v1/ingest/external",
        "post",
        lambda c: c.kb.ingest_external(
            source_type="readwise",
            payload={"highlights": [{"text": "h1", "url": "https://example.com/h1"}]},
            field_mappings={"content": "highlights[].text", "source_uri": "highlights[].url"},
        ),
        IngestExternalResponse,
        {"accepted": 1, "skipped": 0, "errors": [], "source_type": "readwise"},
    ),
    (
        "verify.check",
        "/sdk/v1/hallucination",
        "post",
        lambda c: c.verify.check("The sky is blue.", conversation_id="conv-1"),
        HallucinationResponse,
        {"conversation_id": "conv-1", "skipped": False, "claims": [], "summary": {}},
    ),
    (
        "memory.extract",
        "/sdk/v1/memory/extract",
        "post",
        lambda c: c.memory.extract("I prefer dark mode.", conversation_id="conv-1"),
        MemoryExtractResponse,
        {"conversation_id": "conv-1", "memories_extracted": 1, "memories_stored": 1},
    ),
    (
        "llm.complete",
        "/sdk/v1/llm/complete",
        "post",
        lambda c: c.llm.complete([{"role": "user", "content": "Hi"}], task_type="internal"),
        LLMCompleteResponse,
        {"content": "Yes.", "model": "openai/gpt-4o-mini", "provider": "openrouter_paid"},
    ),
]


@pytest.mark.parametrize(
    "label,path,method,invoke,response_model,response_fixture", POST_CASES, ids=[c[0] for c in POST_CASES]
)
def test_post_request_body_matches_spec(
    label: str,
    path: str,
    method: str,
    invoke: Callable[[CeridClient], Any],
    response_model: type,
    response_fixture: dict[str, Any],
) -> None:
    """The JSON body the SDK method actually sends must validate against the
    spec's requestBody schema (required fields present, types correct).

    This is the check that catches the finance-client class of bug: a
    method that builds its wire payload with the wrong key name for a
    required field looks fine at the type-checker and fails only at
    request time with a 422 the caller has to go debug.
    """
    with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
        mock = _capture_post(client, response_fixture)
        invoke(client)
        body = mock.call_args.kwargs["json"]

    request_schema = _request_schema(path, method)
    Draft202012Validator(request_schema).validate(body)


@pytest.mark.parametrize(
    "label,path,method,invoke,response_model,response_fixture", POST_CASES, ids=[c[0] for c in POST_CASES]
)
def test_post_response_model_covers_required_fields(
    label: str,
    path: str,
    method: str,
    invoke: Callable[[CeridClient], Any],
    response_model: type,
    response_fixture: dict[str, Any],
) -> None:
    response_schema = _response_schema(path, method)
    Draft202012Validator(response_schema).validate(response_fixture)
    _assert_response_model_covers_required(response_model, response_schema, label)

    with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
        _capture_post(client, response_fixture)
        result = invoke(client)
    assert isinstance(result, response_model)


# ---------------------------------------------------------------------------
# GET endpoints: no request body — only response-shape + a real round-trip
# through the mocked transport.
# ---------------------------------------------------------------------------
GetCase = tuple[str, str, str, Callable[[CeridClient], Any], type, dict[str, Any]]

GET_CASES: list[GetCase] = [
    (
        "system.health",
        "/sdk/v1/health",
        "get",
        lambda c: c.system.health(),
        HealthResponse,
        {"status": "healthy", "version": "1.1.0", "services": {"chromadb": "connected"}},
    ),
    (
        "system.settings",
        "/sdk/v1/settings",
        "get",
        lambda c: c.system.settings(),
        SettingsResponse,
        {"version": "1.1.0", "tier": "community", "features": {}},
    ),
    (
        "system.plugins",
        "/sdk/v1/plugins",
        "get",
        lambda c: c.system.plugins(),
        PluginListResponse,
        {"plugins": [], "total": 0},
    ),
    (
        "kb.taxonomy",
        "/sdk/v1/taxonomy",
        "get",
        lambda c: c.kb.taxonomy(),
        TaxonomyResponse,
        {"domains": ["general"], "taxonomy": {}},
    ),
    (
        "memory.get_job",
        "/sdk/v1/memory/extract/jobs/{job_id}",
        "get",
        lambda c: c.memory.get_job("job-123"),
        MemoryExtractJobStatus,
        {"job_id": "job-123", "status": "finished"},
    ),
]


@pytest.mark.parametrize(
    "label,path,method,invoke,response_model,response_fixture", GET_CASES, ids=[c[0] for c in GET_CASES]
)
def test_get_endpoint_matches_spec(
    label: str,
    path: str,
    method: str,
    invoke: Callable[[CeridClient], Any],
    response_model: type,
    response_fixture: dict[str, Any],
) -> None:
    response_schema = _response_schema(path, method)
    Draft202012Validator(response_schema).validate(response_fixture)
    _assert_response_model_covers_required(response_model, response_schema, label)

    with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
        _capture_get(client, response_fixture)
        result = invoke(client)
    assert isinstance(result, response_model)


# ---------------------------------------------------------------------------
# Endpoints with no committed schema (docs/openapi-sdk-v1.json declares them
# with a free-form `{}` schema server-side: /sdk/v1/collections,
# /sdk/v1/health/detailed, /sdk/v1/ingest, /sdk/v1/ingest/file). There is
# nothing for a schema-driven test to assert there; they're covered by the
# existing behavioral tests in test_client.py instead.
# ---------------------------------------------------------------------------


async def _async_capture_post(client: AsyncCeridClient, response_fixture: dict[str, Any]) -> MagicMock:
    mock = MagicMock(return_value=_mock_response(200, response_fixture))

    async def _post(*args: Any, **kwargs: Any) -> httpx.Response:
        mock(*args, **kwargs)
        return mock.return_value

    client._http.post = _post
    return mock


@pytest.mark.asyncio
async def test_async_memory_extract_request_matches_spec() -> None:
    """Regression: AsyncMemoryResource.extract shared the same
    ``text=`` (not ``response_text=``) body-key bug as the sync client
    before this fix — the async path is a separate copy of the same
    construction logic, so it needs its own coverage."""
    async with AsyncCeridClient(base_url="http://localhost:8888", client_id="test") as client:
        mock = await _async_capture_post(
            client, {"conversation_id": "conv-1", "memories_extracted": 1, "memories_stored": 1}
        )
        await client.memory.extract("I prefer dark mode.", conversation_id="conv-1")
        body = mock.call_args.kwargs["json"]

    Draft202012Validator(_request_schema("/sdk/v1/memory/extract", "post")).validate(body)


@pytest.mark.asyncio
async def test_async_verify_check_request_matches_spec() -> None:
    """Regression: AsyncVerifyResource.check shared the same ``response=``
    (not ``response_text=``) body-key bug as the sync client before this fix."""
    async with AsyncCeridClient(base_url="http://localhost:8888", client_id="test") as client:
        mock = await _async_capture_post(
            client, {"conversation_id": "conv-1", "skipped": False, "claims": [], "summary": {}}
        )
        await client.verify.check("The sky is blue.", conversation_id="conv-1")
        body = mock.call_args.kwargs["json"]

    Draft202012Validator(_request_schema("/sdk/v1/hallucination", "post")).validate(body)


def test_sdk_protocol_version_matches_spec_version() -> None:
    """``__version__.py`` documents that SDK_PROTOCOL_VERSION tracks the
    spec's ``info.version`` — this is what actually enforces that promise."""
    assert SDK_PROTOCOL_VERSION == SPEC["info"]["version"], (
        "cerid.__version__.SDK_PROTOCOL_VERSION has drifted from "
        "docs/openapi-sdk-v1.json's info.version — bump both together "
        "(see CONTRIBUTING.md 'SDK contract & versioning')."
    )
