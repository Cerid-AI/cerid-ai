# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""REST surface backing the Sources pane (F1 / F2 / F3).

Endpoints:

* ``GET    /sources``               — list every (:Source) node
* ``GET    /sources/kinds``         — enumerate the 22 supported kinds
* ``POST   /sources``               — create a new source (runs the
  connector's ``connect`` lifecycle, persists the Neo4j node, returns
  the connection-time metric for the FE counter)
* ``GET    /sources/{id}``          — single source detail
* ``POST   /sources/{id}/test``     — re-run ``health_check`` against
  the live connector
* ``DELETE /sources/{id}``          — remove the node + clear cursor

Phase 2B scope. The connectors registered as of this phase are
``rss`` and ``url_watch`` (B2.2 / B2.5); ``webhook`` sources are
created here too but the actual ingest path is the Phase 2A
``POST /sdk/v1/ingest/webhook/{token}`` receiver.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db.neo4j import sources as srcdb
from app.deps import get_neo4j, get_redis
from app.services import sync_cursor, webhook_tokens
from core.ingest.sources.kinds import (
    KIND_FAMILY,
    KIND_TIER,
    SOURCE_KINDS,
)
from core.ingest.sources.registry import get_connector
from core.ingest.telemetry import time_connect
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.routers.sources")

router = APIRouter(prefix="/sources", tags=["sources"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SourceKindMeta(BaseModel):
    kind: str
    family: str
    tier: str  # "core" | "pro"


class CreateSourceRequest(BaseModel):
    kind: str = Field(..., description="One of the 22 supported source kinds")
    display_name: str = Field(..., min_length=1, max_length=128)
    config: dict[str, Any] = Field(default_factory=dict)


class SourceRecord(BaseModel):
    id: str
    kind: str
    family: str
    display_name: str
    tier: str
    status: str
    config: dict[str, Any]
    sync_cursor: dict[str, Any]
    total_artifacts: int = 0
    total_chunks: int = 0
    total_edges: int = 0
    total_artifacts_24h: int = 0
    connection_time_ms: int | None = None
    last_sync_at: str | None = None
    created_at: str | None = None
    last_error: str | None = None


class HealthProbeResult(BaseModel):
    ok: bool
    detail: str = ""
    last_error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_REDACT_KEYS = {"token", "hmac_secret", "client_secret", "api_key", "password", "refresh_token"}


def _redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """Strip credentials before returning a source over the wire.

    The webhook token is the routing key embedded in the share URL,
    so it's surfaced via the dedicated POST /sources/{id}/webhook-url
    endpoint rather than the generic GET; the generic surface always
    redacts. Keeps logs and FE state free of long-lived secrets.
    """
    return {
        k: ("***redacted***" if k in _REDACT_KEYS else v)
        for k, v in config.items()
    }


def _to_record(src: dict[str, Any]) -> SourceRecord:
    return SourceRecord(
        id=src["id"],
        kind=src["kind"],
        family=src.get("family", KIND_FAMILY.get(src["kind"], "files")),
        display_name=src["display_name"],
        tier=src.get("tier", "core"),
        status=src.get("status", "connected"),
        config=_redact_config(src.get("config") or {}),
        sync_cursor=src.get("sync_cursor") or {},
        total_artifacts=src.get("total_artifacts", 0),
        total_chunks=src.get("total_chunks", 0),
        total_edges=src.get("total_edges", 0),
        total_artifacts_24h=src.get("total_artifacts_24h", 0),
        connection_time_ms=src.get("connection_time_ms"),
        last_sync_at=src.get("last_sync_at"),
        created_at=src.get("created_at"),
        last_error=src.get("last_error"),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/kinds", response_model=list[SourceKindMeta])
async def list_source_kinds():
    """Enumerate the 22 supported kinds with family + tier metadata.
    Drives the F1 gallery and the F2 radial menu's family grouping.
    """
    return [
        SourceKindMeta(kind=k, family=KIND_FAMILY[k], tier=KIND_TIER[k])
        for k in SOURCE_KINDS
    ]


@router.get("", response_model=list[SourceRecord])
async def list_sources(kind: str | None = None):
    """List every Source, newest first. Optional ?kind= filter."""
    if kind is not None and kind not in SOURCE_KINDS:
        raise HTTPException(status_code=422, detail=f"Unknown source kind: {kind}")
    rows = srcdb.list_sources(get_neo4j(), kind=kind)
    return [_to_record(r) for r in rows]


@router.get("/{source_id}", response_model=SourceRecord)
async def get_source(source_id: str):
    src = srcdb.get_source(get_neo4j(), source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return _to_record(src)


@router.post("", response_model=SourceRecord, status_code=201)
async def create_source(body: CreateSourceRequest):
    """Create a new (:Source) node.

    For connector-backed kinds (rss, url_watch, …) we invoke the
    connector's ``connect`` lifecycle to validate config and emit a
    real connection-time metric. Webhook-kind sources skip the
    connect step and instead mint a token + optional HMAC secret —
    the receiver endpoint handles the inbound traffic.
    """
    kind = body.kind
    if kind not in SOURCE_KINDS:
        raise HTTPException(status_code=422, detail=f"Unknown source kind: {kind}")

    tier = KIND_TIER[kind]
    family = KIND_FAMILY[kind]

    # Webhook kind: mint token, skip connector lifecycle
    if kind == "webhook":
        token = webhook_tokens.generate_token()
        config_to_store: dict[str, Any] = {"token": token, **body.config}
        if body.config.get("require_hmac"):
            config_to_store["hmac_secret"] = webhook_tokens.generate_hmac_secret()
        src = srcdb.create_source(
            get_neo4j(),
            kind=kind,
            family=family,
            display_name=body.display_name,
            config=config_to_store,
            tier=tier,
            connection_time_ms=0,
        )
        return _to_record(src)

    # Connector-backed kind: run connect()
    connector = get_connector(kind)  # type: ignore[arg-type]
    if connector is None:
        raise HTTPException(
            status_code=501,
            detail=f"Connector for kind={kind!r} not yet implemented",
        )

    try:
        with time_connect() as timer:
            result = await connector.connect(body.config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        log_swallowed_error("sources.create_source.connect", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Connector failed to connect: {exc}",
        ) from exc

    src = srcdb.create_source(
        get_neo4j(),
        kind=kind,
        family=family,
        display_name=body.display_name,
        config=result.config,
        tier=tier,
        connection_time_ms=result.connection_time_ms or timer.elapsed_ms,
    )

    # Initial cursor seed
    if result.initial_cursor:
        try:
            sync_cursor.set_cursor(
                get_redis(), get_neo4j(), src["id"], result.initial_cursor,
            )
        except Exception as exc:
            log_swallowed_error("sources.create_source.set_cursor", exc)

    return _to_record(src)


@router.post("/{source_id}/test", response_model=HealthProbeResult)
async def test_source(source_id: str):
    """Re-run the connector's ``health_check`` against the live source."""
    src = srcdb.get_source(get_neo4j(), source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    if src["kind"] == "webhook":
        # No remote system to probe — receivers are inbound-only.
        return HealthProbeResult(ok=True, detail="Webhook receiver active")

    connector = get_connector(src["kind"])
    if connector is None:
        raise HTTPException(
            status_code=501,
            detail=f"Connector for kind={src['kind']!r} not yet implemented",
        )

    try:
        probe = await connector.health_check(source_id, src.get("config") or {})
    except Exception as exc:
        log_swallowed_error("sources.test_source", exc)
        return HealthProbeResult(ok=False, detail="probe raised", last_error=str(exc))

    return HealthProbeResult(ok=probe.ok, detail=probe.detail, last_error=probe.last_error)


@router.delete("/{source_id}", status_code=204)
async def delete_source(source_id: str, cascade: bool = False):
    """Remove a Source node. ``?cascade=true`` also drops FROM_SOURCE
    edges; artifact nodes themselves survive."""
    src = srcdb.get_source(get_neo4j(), source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Best-effort disconnect on the live connector before deletion
    if src["kind"] != "webhook":
        connector = get_connector(src["kind"])
        if connector is not None:
            try:
                await connector.disconnect(source_id, src.get("config") or {})
            except Exception as exc:
                log_swallowed_error("sources.delete_source.disconnect", exc)

    try:
        sync_cursor.clear_cursor(get_redis(), get_neo4j(), source_id)
    except Exception as exc:
        log_swallowed_error("sources.delete_source.clear_cursor", exc)

    srcdb.delete_source(get_neo4j(), source_id, cascade=cascade)
    return None
