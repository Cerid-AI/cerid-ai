# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract tests: pin the Python SDK against docs/openapi-sdk-v1.json.

``docs/openapi-sdk-v1.json`` is generated from the live FastAPI routes by
``scripts/gen_sdk_openapi.py`` and is the authoritative ``/sdk/v1/*``
contract (``scripts/gen_sdk_openapi.py --check``, a step in CI's ``lint``
job, keeps it byte-for-byte in sync with the server). These tests are the other half of that pin: for every
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
    MemoryExtractAcceptedResponse,
    MemoryExtractJobStatus,
    MemoryExtractResponse,
    MemoryRecallResponse,
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
    """The SDK model must type every field the spec declares on the response.

    Checking only the spec's ``required`` list is not enough: the server gives
    most response fields a default, so a newly-added field is never
    ``required`` and an SDK model that omits it passes the gate while its
    consumers get an untyped extra they cannot discover from autocomplete
    (``mode`` and ``nli_skipped`` shipped that way).
    """
    spec_fields = set(response_schema.get("required", [])) | set(response_schema.get("properties", {}))
    declared = set(response_model.model_fields)
    missing = spec_fields - declared
    assert not missing, (
        f"{label}: spec declares {sorted(missing)} on the response but "
        f"{response_model.__name__} doesn't"
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
        {
            "conversation_id": "conv-1",
            "skipped": False,
            "claims": [{"claim": "The sky is blue.", "status": "verified", "confidence": 0.91}],
            # The server emits a float ``overall_confidence`` alongside the
            # integer per-status counts (core/agents/hallucination/streaming.py).
            # An empty-dict fixture here is what let a Dict[str, int] annotation
            # ship: every real response would have failed validation.
            "summary": {"total": 1, "verified": 1, "assessed": 1, "overall_confidence": 0.833},
            "mode": "thorough",
            "nli_skipped": False,
        },
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
        "memory.recall",
        "/sdk/v1/memory/recall",
        "post",
        lambda c: c.memory.recall("bills", top_k=5),
        MemoryRecallResponse,
        {"memories": [], "total": 0},
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
        {"status": "healthy", "version": "1.2.0", "services": {"chromadb": "connected"}},
    ),
    (
        "system.settings",
        "/sdk/v1/settings",
        "get",
        lambda c: c.system.settings(),
        SettingsResponse,
        {"version": "1.2.0", "tier": "community", "features": {}},
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
# SDK-not-stricter-than-spec: the server declares SdkSettingsResponse,
# SdkTaxonomyResponse, and SdkPluginsResponse with Any-typed fields (the
# "generated: single-return dict-literal routes" block in app/routers/sdk.py),
# so the pinned schemas constrain field *presence* but not field *types*.
# An SDK model stricter than the pin turns a contract-legal 200 into a
# client-side ValidationError. Each fixture below is first validated against
# the pinned schema — proving the payload is one the server may legitimately
# send — and must then be accepted by the SDK model.
# ---------------------------------------------------------------------------
LOOSE_CASES: list[tuple[str, str, type, dict[str, Any]]] = [
    (
        "settings.untyped-fields",
        "/sdk/v1/settings",
        SettingsResponse,
        {"version": 2, "tier": None, "features": {"private_mode": "beta"}},
    ),
    (
        "taxonomy.untyped-fields",
        "/sdk/v1/taxonomy",
        TaxonomyResponse,
        {"domains": {"general": {}}, "taxonomy": None},
    ),
    (
        "plugins.untyped-items-and-total",
        "/sdk/v1/plugins",
        PluginListResponse,
        {"plugins": ["audio", 3], "total": None},
    ),
]


@pytest.mark.parametrize("label,path,response_model,payload", LOOSE_CASES, ids=[c[0] for c in LOOSE_CASES])
def test_response_model_accepts_everything_the_spec_allows(
    label: str,
    path: str,
    response_model: type,
    payload: dict[str, Any],
) -> None:
    schema = _response_schema(path, "get")
    Draft202012Validator(schema).validate(payload)  # contract-legal by the pin
    response_model.model_validate(payload)  # must not raise


def test_plugins_model_still_requires_a_list() -> None:
    """The pin does type ``plugins`` as an array — that constraint must hold."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        PluginListResponse.model_validate({"plugins": "not-a-list", "total": 0})


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
            client,
            {
                "conversation_id": "conv-1",
                "skipped": False,
                "claims": [],
                "summary": {"total": 0, "overall_confidence": 0.0},
            },
        )
        await client.verify.check("The sky is blue.", conversation_id="conv-1")
        body = mock.call_args.kwargs["json"]

    Draft202012Validator(_request_schema("/sdk/v1/hallucination", "post")).validate(body)


# ---------------------------------------------------------------------------
# No-dead-parameters. A key the server does not read is worse than a missing
# one: pydantic's default ``extra='ignore'`` drops it silently, so a typed,
# documented SDK argument can do nothing at all and no test notices. For the
# two endpoints whose body is an untyped ``dict`` there is no schema to check
# against, so the keys the handler actually reads are listed here.
# ---------------------------------------------------------------------------
FREE_FORM_SERVER_KEYS: dict[str, set[str]] = {
    # app/routers/sdk.py::sdk_ingest — content / domain / metadata, with tags
    # folded into metadata.
    "/sdk/v1/ingest": {"content", "domain", "tags", "metadata"},
    # app/routers/sdk.py::sdk_ingest_file — file_path / domain / tags /
    # categorize_mode.
    "/sdk/v1/ingest/file": {"file_path", "domain", "tags", "categorize_mode"},
}

# Every row passes *every* keyword the method accepts — a parameter that only
# appears in a call nobody writes is exactly how four inert arguments shipped.
INGEST_FIXTURE = {"status": "success", "artifact_id": "a1", "chunks": 1, "domain": "databases"}

DEAD_PARAM_CASES: list[tuple[str, str, Callable[[CeridClient], Any], dict[str, Any]]] = [
    (
        "kb.query",
        "/sdk/v1/query",
        lambda c: c.kb.query("q", domains=["general"], top_k=5),
        {"context": "c", "sources": [], "confidence": 0.5},
    ),
    (
        "kb.search",
        "/sdk/v1/search",
        lambda c: c.kb.search("q", domain="general", top_k=3),
        {"results": [], "total_results": 0, "confidence": 0.0},
    ),
    (
        "kb.ingest",
        "/sdk/v1/ingest",
        lambda c: c.kb.ingest("PostgreSQL uses MVCC.", domain="databases", tags="pg", metadata={"title": "T"}),
        INGEST_FIXTURE,
    ),
    (
        "kb.ingest_file",
        "/sdk/v1/ingest/file",
        lambda c: c.kb.ingest_file(
            "/archive/notes.md", domain="databases", tags="pg", categorize_mode="manual",
        ),
        INGEST_FIXTURE,
    ),
    (
        "kb.ingest_external",
        "/sdk/v1/ingest/external",
        lambda c: c.kb.ingest_external(
            source_type="readwise",
            payload={"highlights": []},
            field_mappings={"content": "highlights[].text"},
        ),
        {"accepted": 0, "skipped": 0, "errors": [], "source_type": "readwise"},
    ),
    (
        "verify.check",
        "/sdk/v1/hallucination",
        lambda c: c.verify.check("The sky is blue.", conversation_id="conv-1"),
        {"conversation_id": "conv-1", "skipped": False, "claims": [], "summary": {}},
    ),
    (
        "memory.extract",
        "/sdk/v1/memory/extract",
        lambda c: c.memory.extract("I prefer dark mode.", conversation_id="conv-1"),
        {"conversation_id": "conv-1", "memories_extracted": 0, "memories_stored": 0},
    ),
    (
        "llm.complete",
        "/sdk/v1/llm/complete",
        lambda c: c.llm.complete(
            [{"role": "user", "content": "Hi"}],
            task_type="internal",
            query="Hi",
            cost_sensitivity="high",
            temperature=0.1,
            max_tokens=10,
            response_format={"type": "json_object"},
            slo_budget_ms=5000,
        ),
        {"content": "Yes.", "model": "m", "provider": "openrouter_paid"},
    ),
]


@pytest.mark.parametrize("label,path,invoke,response_fixture", DEAD_PARAM_CASES, ids=[c[0] for c in DEAD_PARAM_CASES])
def test_request_body_carries_no_key_the_server_ignores(
    label: str,
    path: str,
    invoke: Callable[[CeridClient], Any],
    response_fixture: dict[str, Any],
) -> None:
    with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
        mock = _capture_post(client, response_fixture)
        invoke(client)
        body = mock.call_args.kwargs["json"]

    accepted = set(_request_schema(path, "post").get("properties", {})) or FREE_FORM_SERVER_KEYS[path]
    ignored = set(body) - accepted
    assert not ignored, (
        f"{label}: sends {sorted(ignored)}, which the server drops on the floor "
        "— wire the field server-side or take the parameter off the SDK method"
    )


# ---------------------------------------------------------------------------
# Default-invocation coverage. Every POST_CASES row passes conversation_id
# explicitly, so the *documented default* call — the one every README shows —
# was never exercised: it omitted a field the server marks required and
# 422'd. A spec-required field must be unskippable at the call site.
# ---------------------------------------------------------------------------
DEFAULT_CALL_CASES: list[tuple[str, str, Callable[[CeridClient], Any], dict[str, Any]]] = [
    (
        "verify.check",
        "/sdk/v1/hallucination",
        lambda c: c.verify.check("The sky is blue."),  # type: ignore[call-arg]
        {"conversation_id": "", "skipped": False, "claims": [], "summary": {}},
    ),
    (
        "memory.extract",
        "/sdk/v1/memory/extract",
        lambda c: c.memory.extract("I prefer dark mode."),  # type: ignore[call-arg]
        {"conversation_id": "", "memories_extracted": 0, "memories_stored": 0},
    ),
]


@pytest.mark.parametrize("label,path,invoke,response_fixture", DEFAULT_CALL_CASES, ids=[c[0] for c in DEFAULT_CALL_CASES])
def test_minimal_invocation_cannot_omit_a_spec_required_field(
    label: str,
    path: str,
    invoke: Callable[[CeridClient], Any],
    response_fixture: dict[str, Any],
) -> None:
    """Omitting a server-required argument must fail at the call site.

    Either the SDK refuses the call (TypeError) or it supplies a value — what
    it must not do is put a body the server will reject on the wire and let
    the caller discover it as a 422 in production.
    """
    with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
        mock = _capture_post(client, response_fixture)
        try:
            invoke(client)
        except TypeError:
            return
        body = mock.call_args.kwargs["json"]

    Draft202012Validator(_request_schema(path, "post")).validate(body)


def test_search_can_drop_knowledge_packs() -> None:
    """``exclude_packs`` is the personal-first search switch — answer from the
    operator's own data rather than bundled knowledge packs. The server has
    taken it since SDKSearchRequest gained the field; neither SDK could send
    it, so the mode was unreachable from a published client."""
    with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
        mock = _capture_post(client, {"results": [], "total_results": 0, "confidence": 0.0})
        client.kb.search("q", exclude_packs=True)
        body = mock.call_args.kwargs["json"]

    assert body["exclude_packs"] is True
    Draft202012Validator(_request_schema("/sdk/v1/search", "post")).validate(body)


# ---------------------------------------------------------------------------
# The 202 async envelope. MEMORY_QUEUE_MODE=async is the default on
# local-inference installs, so this is the ordinary path there — not an edge.
# ---------------------------------------------------------------------------


def test_memory_extract_202_returns_the_accepted_envelope() -> None:
    """A queued extraction must not read back as a finished one with no memories.

    The server answers 202 with a job_id and a status_url; parsing that as
    ``MemoryExtractResponse`` renders queued work as ``extracted 0, stored 0``
    and throws away the only handle the caller has on the job.
    """
    accepted = {
        "job_id": "job-42",
        "status": "queued",
        "status_url": "/sdk/v1/memory/extract/jobs/job-42",
        "conversation_id": "conv-1",
    }
    Draft202012Validator(_response_schema("/sdk/v1/memory/extract", "post", "202")).validate(accepted)

    with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
        client._http.post = MagicMock(return_value=_mock_response(202, accepted))
        result = client.memory.extract("I prefer dark mode.", conversation_id="conv-1")

    assert isinstance(result, MemoryExtractAcceptedResponse), (
        f"202 Accepted parsed as {type(result).__name__} — the caller never "
        "learns a job_id exists and never polls get_job"
    )
    assert result.job_id == "job-42"
    assert result.status_url.endswith("job-42")


def test_memory_extract_200_still_returns_the_sync_result() -> None:
    body = {"conversation_id": "conv-1", "memories_extracted": 2, "memories_stored": 2}
    with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
        client._http.post = MagicMock(return_value=_mock_response(200, body))
        result = client.memory.extract("I prefer dark mode.", conversation_id="conv-1")

    assert isinstance(result, MemoryExtractResponse)
    assert result.memories_stored == 2


@pytest.mark.asyncio
async def test_async_memory_extract_202_returns_the_accepted_envelope() -> None:
    accepted = {"job_id": "job-43", "status": "queued", "status_url": "/sdk/v1/memory/extract/jobs/job-43"}

    async with AsyncCeridClient(base_url="http://localhost:8888", client_id="test") as client:
        async def _post(*args: Any, **kwargs: Any) -> httpx.Response:
            return _mock_response(202, accepted)

        client._http.post = _post
        result = await client.memory.extract("I prefer dark mode.", conversation_id="conv-1")

    assert isinstance(result, MemoryExtractAcceptedResponse)
    assert result.job_id == "job-43"


def test_sdk_protocol_version_matches_spec_version() -> None:
    """``__version__.py`` documents that SDK_PROTOCOL_VERSION tracks the
    spec's ``info.version`` — this is what actually enforces that promise."""
    assert SDK_PROTOCOL_VERSION == SPEC["info"]["version"], (
        "cerid.__version__.SDK_PROTOCOL_VERSION has drifted from "
        "docs/openapi-sdk-v1.json's info.version — bump both together "
        "(see CONTRIBUTING.md 'SDK contract & versioning')."
    )
