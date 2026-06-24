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
* ``POST   /sources/{id}/policy``   — patch retention_policy / quality_floor
* ``GET    /sources/{id}/webhook-url`` — webhook source's receiver URL

Webhook sources are created here, but inbound traffic flows through
``POST /sdk/v1/ingest/webhook/{token}`` (the security boundary).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.db.neo4j import sources as srcdb
from app.deps import get_neo4j, get_redis
from app.services import sync_cursor, webhook_tokens
from app.services.watched_folders_bridge import (
    create_folder_source,
    delete_folder_source,
    folder_health,
    get_folder_source,
    list_folder_sources,
    update_folder_source,
)
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
    availability: str = "coming_soon"  # "available" | "oauth" | "coming_soon"
    providers: list[str] = []  # webhook-backed kinds: the recipe providers to pick


def _is_webhook_backed(kind: str) -> bool:
    """Inbound webhook-fed kinds (``webhook``, ``chat_capture``, ``dev_events``).

    These have no external *connect* step — the receiver endpoint
    (``POST /sdk/v1/ingest/webhook/{token}``) handles inbound traffic and the
    adapter recipe (resolved by provider) normalizes it. So they are created by
    minting a token, not by a SourceConnector lifecycle. Derived from the family
    map so a future webhook-backed kind is covered automatically.
    """
    return KIND_FAMILY.get(kind) == "webhook"  # type: ignore[call-overload]


def _kind_providers(kind: str) -> list[str]:
    """Recipe providers available for a webhook-backed kind (for the wizard
    picker + create-time validation). Empty for non-recipe kinds."""
    import core.ingest.adapters as _adapters  # noqa: F401 — registers recipes
    from core.ingest.adapters.registry import providers_for_kind

    return providers_for_kind(kind)


def _kind_availability(kind: str, oauth_kinds: set[str]) -> str:
    """Capability flag for a source kind, so the wizard can gate kinds that
    have no working ingestion path (rather than letting POST /sources 501).

    - ``available``   — a SourceConnector is registered, a webhook-backed kind,
                        or the ``folder`` kind (bridge-backed via watched-folders).
    - ``oauth``       — connectable via the /connectors OAuth flow (Gmail, etc.).
    - ``coming_soon`` — declared in SOURCE_KINDS but not yet implemented.
    """
    # folder is bridge-backed (watched-folders store); no connector registered.
    if kind == "folder":
        return "available"

    import core.ingest.sources.connectors as _conns  # noqa: F401 — registers connectors
    from core.ingest.sources.registry import get_connector

    if _is_webhook_backed(kind) or get_connector(kind) is not None:  # type: ignore[arg-type]
        return "available"
    if kind in oauth_kinds:
        return "oauth"
    return "coming_soon"


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
    quality_floor: float = 0.0


class HealthProbeResult(BaseModel):
    ok: bool
    detail: str = ""
    last_error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_REDACT_KEYS = {"token", "hmac_secret", "client_secret", "api_key", "password", "refresh_token"}
_REDACT_MASK = "***redacted***"


def _redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """Strip credentials before returning a source over the wire.

    The webhook token is the routing key embedded in the share URL,
    so it's surfaced via the dedicated POST /sources/{id}/webhook-url
    endpoint rather than the generic GET; the generic surface always
    redacts. Keeps logs and FE state free of long-lived secrets.
    """
    return {
        k: (_REDACT_MASK if k in _REDACT_KEYS else v)
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
        quality_floor=float(src.get("quality_floor", 0.0) or 0.0),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/kinds", response_model=list[SourceKindMeta])
async def list_source_kinds():
    """Enumerate the 22 supported kinds with family + tier metadata.
    Drives the F1 gallery and the F2 radial menu's family grouping.
    """
    from app.routers.connectors import oauth_connector_kinds

    oauth_kinds = oauth_connector_kinds()
    return [
        SourceKindMeta(
            kind=k,
            family=KIND_FAMILY[k],
            tier=KIND_TIER[k],
            availability=_kind_availability(k, oauth_kinds),
            providers=_kind_providers(k),
        )
        for k in SOURCE_KINDS
    ]


@router.get("", response_model=list[SourceRecord])
async def list_sources(kind: str | None = None):
    """List every Source, newest first. Optional ?kind= filter."""
    if kind is not None and kind not in SOURCE_KINDS:
        raise HTTPException(status_code=422, detail=f"Unknown source kind: {kind}")
    rows = [_to_record(r) for r in srcdb.list_sources(get_neo4j(), kind=kind)]
    if kind in (None, "folder"):
        rows += [SourceRecord(**s) for s in list_folder_sources(get_redis())]
    return rows


@router.get("/{source_id}", response_model=SourceRecord)
async def get_source(source_id: str):
    if source_id.startswith("folder:"):
        proj = get_folder_source(get_redis(), source_id)
        if proj is None:
            raise HTTPException(status_code=404, detail="Source not found")
        return SourceRecord(**proj)
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

    # Folder kind: delegate to the watched-folders store (preserves path
    # validation + _ALLOWED_ROOTS + vault_write Redis coupling).
    if kind == "folder":
        try:
            proj = await create_folder_source(get_redis(), body.display_name, body.config)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            log_swallowed_error("sources.create_source.folder", exc)
            raise HTTPException(status_code=502, detail=f"Folder creation failed: {exc}") from exc
        return SourceRecord(**proj)

    tier = KIND_TIER[kind]
    family = KIND_FAMILY[kind]

    # Webhook-backed kinds (webhook, chat_capture, dev_events): mint a token,
    # skip the connector lifecycle. Inbound traffic flows through the receiver
    # endpoint, which resolves the adapter recipe by provider.
    if _is_webhook_backed(kind):
        config_in = dict(body.config)
        requires_sig = False
        # Typed webhook-backed kinds need a provider with a registered recipe —
        # otherwise inbound payloads can't be normalized and the source is
        # dead-on-arrival. The generic `webhook` kind stays permissive (raw
        # pass-through), so provider is optional there.
        if kind != "webhook":
            provider = (config_in.get("provider") or "").strip()
            valid = _kind_providers(kind)
            if not provider:
                raise HTTPException(
                    status_code=422,
                    detail=f"kind={kind!r} requires config.provider (one of {valid})",
                )
            from core.ingest.adapters.registry import get_recipe
            recipe = get_recipe(kind, provider)
            if recipe is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown provider {provider!r} for kind={kind!r}; valid: {valid}",
                )
            requires_sig = bool(getattr(recipe, "requires_signature", False))

        token = webhook_tokens.generate_token()
        config_to_store: dict[str, Any] = {"token": token, **config_in}
        # Mint an HMAC secret when the caller opts in OR the provider mandates it
        # (github / stripe set requires_signature) — without it the receiver
        # rejects that provider's traffic as "mandated but unconfigured".
        if config_in.get("require_hmac") or requires_sig:
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


_CLIPBOARD_HEARTBEAT_KEY = "cerid:clipboard:alive"
_CLIPBOARD_STALE_AFTER_S = 90


def _check_clipboard_daemon() -> HealthProbeResult:
    """Read the host-daemon heartbeat from Redis. Combined with the
    connector's basic check in :func:`test_source` so the clipboard
    surface accurately reports whether the host daemon is running.
    """
    import time

    from app.deps import get_redis

    try:
        raw = get_redis().get(_CLIPBOARD_HEARTBEAT_KEY)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("sources.clipboard_heartbeat", exc)
        return HealthProbeResult(ok=False, detail="redis error", last_error=str(exc))

    if raw is None:
        return HealthProbeResult(
            ok=False,
            detail="daemon heartbeat absent (is the host daemon running?)",
        )
    try:
        last = int(raw)
    except (TypeError, ValueError):
        return HealthProbeResult(ok=False, detail="malformed heartbeat value")

    age = int(time.time()) - last
    if age > _CLIPBOARD_STALE_AFTER_S:
        return HealthProbeResult(ok=False, detail=f"heartbeat stale ({age}s old)")
    return HealthProbeResult(ok=True, detail=f"heartbeat {age}s ago")


@router.post("/{source_id}/test", response_model=HealthProbeResult)
async def test_source(source_id: str):
    """Re-run the connector's ``health_check`` against the live source."""
    if source_id.startswith("folder:"):
        fprobe = await folder_health(get_redis(), source_id)
        return HealthProbeResult(**fprobe)

    src = srcdb.get_source(get_neo4j(), source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    if _is_webhook_backed(src["kind"]):
        # No remote system to probe — receivers are inbound-only.
        return HealthProbeResult(ok=True, detail="Webhook receiver active")

    if src["kind"] == "clipboard":
        # Decorated check: connector's basic status + Redis heartbeat.
        return _check_clipboard_daemon()

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


@router.get("/{source_id}/webhook-url")
async def get_webhook_url(source_id: str, request: Request):
    """Return the live webhook receiver URL (with token) for a
    webhook-kind source. Drives the F7 share card — the only place
    we deliberately surface the token in cleartext.
    """
    src = srcdb.get_source(get_neo4j(), source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if not _is_webhook_backed(src["kind"]):
        raise HTTPException(status_code=422, detail="Not a webhook source")
    config = src.get("config") or {}
    token = config.get("token") if isinstance(config, dict) else None
    if not token:
        raise HTTPException(status_code=500, detail="Source missing token")
    base = str(request.base_url).rstrip("/")
    url = f"{base}/sdk/v1/ingest/webhook/{token}"
    require_hmac = bool(config.get("hmac_secret"))
    return {
        "url": url,
        "require_hmac": require_hmac,
        "curl_example": (
            f"curl -X POST '{url}' -H 'Content-Type: application/json' "
            f"-d '{{\"content\": \"hello from cerid\"}}'"
        ),
    }


class ConfigPatch(BaseModel):
    """Payload for ``POST /sources/{id}/config`` — inline config editing
    from the source-detail pane. Any value equal to ``"***redacted***"``
    is treated as a no-op placeholder and dropped before merging so
    callers never overwrite a stored secret with the display mask.
    """

    config: dict[str, Any]


@router.post("/{source_id}/config", response_model=SourceRecord)
async def update_source_config(source_id: str, body: ConfigPatch):
    """Patch a source's config; re-runs the connector validation lifecycle.

    Folder sources are handled by Stage C2; this endpoint returns 501 for
    them so C2 can replace the branch with the real write when it lands.

    For all other kinds:
    1. Load the stored source (404 if missing).
    2. Merge the incoming patch, dropping any field whose value equals the
       redaction mask ``"***redacted***"`` so callers can echo back the
       FE-safe view without overwriting real secrets.
    3. Re-validate by running the connector's ``connect()`` lifecycle (for
       connector-backed kinds) — invalid edits are rejected as 422 before
       any write. Webhook-backed kinds skip connect (no external system to
       probe) but still persist the merge.
    4. Persist via ``srcdb.update_source_config`` and return the
       re-redacted record.
    """
    if source_id.startswith("folder:"):
        proj = await update_folder_source(get_redis(), source_id, body.config)
        return SourceRecord(**proj)

    src = srcdb.get_source(get_neo4j(), source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    stored_config: dict[str, Any] = src.get("config") or {}
    # Drop any value equal to the redaction mask — never overwrite a real
    # credential with the display placeholder.
    incoming = {k: v for k, v in body.config.items() if v != _REDACT_MASK}
    merged = {**stored_config, **incoming}

    kind: str = src["kind"]

    if _is_webhook_backed(kind):
        # Webhook-backed kinds have no remote system to probe; skip connect.
        # Preserve the minted token and hmac_secret from the stored config —
        # callers cannot replace them via this endpoint (they're redacted).
        updated = srcdb.update_source_config(get_neo4j(), source_id, merged)
        return _to_record(updated)

    connector = get_connector(kind)  # type: ignore[arg-type]
    if connector is None:
        raise HTTPException(
            status_code=501,
            detail=f"Connector for kind={kind!r} not yet implemented",
        )

    try:
        result = await connector.connect(merged)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        log_swallowed_error("sources.update_source_config.connect", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Connector failed to re-validate: {exc}",
        ) from exc

    updated = srcdb.update_source_config(get_neo4j(), source_id, result.config)
    return _to_record(updated)


class PolicyPatch(BaseModel):
    """Payload for ``POST /sources/{id}/policy`` — retention + quality-
    floor editing from the source-detail pane sliders.
    """

    retention_policy: dict[str, Any] | None = None
    quality_floor: float | None = Field(None, ge=0.0, le=1.0)


@router.post("/{source_id}/policy", response_model=SourceRecord)
async def update_source_policy(source_id: str, body: PolicyPatch):
    """Patch the source's retention_policy and/or quality_floor.

    Drives the F4 detail-pane sliders. Both fields are optional;
    callers send only what changed. Validates retention_policy
    against the modes core.ingest.retention knows about.

    For folder sources: folders carry no retention policy in Plan 1 —
    return the projected record unchanged (no-op).
    """
    if source_id.startswith("folder:"):
        proj = get_folder_source(get_redis(), source_id)
        if proj is None:
            raise HTTPException(status_code=404, detail="Source not found")
        return SourceRecord(**proj)

    src = srcdb.get_source(get_neo4j(), source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    if body.retention_policy is not None:
        mode = body.retention_policy.get("mode")
        if mode not in ("keep_all", "days", "count"):
            raise HTTPException(
                status_code=422,
                detail=f"Unknown retention mode: {mode!r}",
            )
        if mode == "days":
            try:
                days = int(body.retention_policy.get("days", 0))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="days must be int") from exc
            if days < 0:
                raise HTTPException(status_code=422, detail="days must be ≥ 0")
        if mode == "count":
            try:
                _max = int(body.retention_policy.get("max", 0))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="max must be int") from exc
            if _max < 0:
                raise HTTPException(status_code=422, detail="max must be ≥ 0")

        import json as _json

        with get_neo4j().session() as session:
            session.run(
                """
                MATCH (s:Source {id: $id})
                SET s.retention_policy = $policy
                """,
                id=source_id,
                policy=_json.dumps(body.retention_policy),
            )

    if body.quality_floor is not None:
        from app.services.quality_floors import set_source_quality_floor

        set_source_quality_floor(source_id, body.quality_floor)

    refreshed = srcdb.get_source(get_neo4j(), source_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Source disappeared after update")
    return _to_record(refreshed)


@router.delete("/{source_id}", status_code=204)
async def delete_source(source_id: str, cascade: bool = False):
    """Remove a Source node. ``?cascade=true`` also drops FROM_SOURCE
    edges; artifact nodes themselves survive."""
    if source_id.startswith("folder:"):
        delete_folder_source(get_redis(), source_id)
        return None

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
