# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""RFC-9110 ``Idempotency-Key`` support for the /sdk/v1 write surface.

External backends (cerid-trading-agent, cerid-boardroom, …) retry on 5xx.
Without idempotency a retried ``POST /sdk/v1/ingest`` double-ingests. A write
handler opts in by wrapping its side-effecting work in :func:`idempotent`::

    result = await idempotent(request, lambda: ingest_content(...))

Semantics (additive — a pure no-op when the header is absent):

* The cache key is ``(X-Client-ID, request path, Idempotency-Key)`` so a key
  is scoped per client and per endpoint and can never collide across them.
* **Lock-first** (this is the correctness-critical part): we ``SET NX`` a
  short-lived *sentinel* before running the work. Exactly one concurrent
  caller wins the SET and runs the work; the result then overwrites the
  sentinel under a 24h TTL. A losing concurrent caller observes the sentinel
  and returns ``409`` (request in flight) rather than re-running the work and
  returning a divergent, uncached result. This closes the GET-then-SET race a
  naive cache-aside has.
* On a HIT (a stored result) the previous successful response is returned
  verbatim and the work does NOT run.
* On the winner's failure the sentinel is **released** (deleted) so a retry
  after the failure re-processes — only successful results are ever cached.
* Redis is best-effort: any Redis error (or an unconfigured client) means the
  work simply runs without idempotency. This layer never raises a 500 of its
  own. The one status it raises is ``409`` for an in-flight duplicate.

Mirrors the ``redis.set(key, val, nx=True, ex=...)`` SETNX pattern used by
``app/processor/subscribers/wiki_refresh.py`` and reuses the shared, synchronous
``app.deps.get_redis`` singleton (``decode_responses=True`` → ``get`` returns
``str``).
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder

from app.deps import get_redis
from core.utils.swallowed import log_swallowed_error

_MODULE = "app.middleware.idempotency"
_KEY_FMT = "cerid:sdk:idem:{client_id}:{path}:{key}"
_HEADER = "Idempotency-Key"
_RESULT_TTL_S = 24 * 60 * 60  # 24h — RFC-9110 recommends a finite window.
_LOCK_TTL_S = 300  # sentinel auto-expires so a crashed winner can't wedge a key.
# Distinctive, never a valid JSON document → never confused with a stored result.
_SENTINEL = "__cerid_idem_inflight__"


def _cache_key(client_id: str, path: str, idem_key: str) -> str:
    return _KEY_FMT.format(client_id=client_id, path=path, key=idem_key)


async def idempotent(
    request: Request,
    work: Callable[[], Awaitable[Any] | Any],
) -> Any:
    """Run ``work`` at most once per ``(client, path, Idempotency-Key)``.

    No header → ``work`` runs unchanged. Redis unavailable → ``work`` runs
    without caching. Concurrent duplicate in flight → ``409``.
    """
    idem_key = request.headers.get(_HEADER)
    if not idem_key:
        return await _run(work)

    redis_client = _safe_redis()
    if redis_client is None:
        return await _run(work)

    client_id = request.headers.get("x-client-id", "gui")
    key = _cache_key(client_id, request.url.path, idem_key)

    acquired = _safe_setnx(redis_client, key)
    if acquired is None:
        # Redis hiccuped acquiring the lock — degrade to a plain run.
        return await _run(work)

    if acquired:
        # We own the key: run, then persist the result or release on failure.
        try:
            result = await _run(work)
        except BaseException:
            _safe_delete(redis_client, key)
            raise
        _store_or_release(redis_client, key, result)
        return result

    # Key already exists: an in-flight sentinel or a stored result.
    cached = _safe_get(redis_client, key)
    if cached is None:
        # Winner released the key between our SETNX and GET → safe to reprocess.
        return await _run(work)
    if cached == _SENTINEL:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "idempotency_key_in_flight",
                "message": (
                    "A request with this Idempotency-Key is still being "
                    "processed. Retry after it completes."
                ),
            },
        )
    try:
        return json.loads(cached)
    except (TypeError, ValueError) as exc:
        # Corrupt cache entry — treat as a miss and reprocess.
        log_swallowed_error(_MODULE, exc, context={"key": key})
        return await _run(work)


async def _run(work: Callable[[], Awaitable[Any] | Any]) -> Any:
    out = work()
    if isinstance(out, Awaitable):
        return await out
    return out


def _safe_redis() -> Any | None:
    try:
        return get_redis()
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(_MODULE, exc)
        return None


def _safe_setnx(redis_client: Any, key: str) -> bool | None:
    """SET NX the sentinel. Returns True (won), False (exists), None (error)."""
    try:
        return bool(redis_client.set(key, _SENTINEL, nx=True, ex=_LOCK_TTL_S))
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(_MODULE, exc, context={"key": key})
        return None


def _safe_get(redis_client: Any, key: str) -> str | None:
    try:
        return redis_client.get(key)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(_MODULE, exc, context={"key": key})
        return None


def _safe_delete(redis_client: Any, key: str) -> None:
    try:
        redis_client.delete(key)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(_MODULE, exc, context={"key": key})


def _store_or_release(redis_client: Any, key: str, result: Any) -> None:
    """Overwrite the sentinel with the serialized result (24h TTL).

    If the result is not JSON-serializable we cannot cache it; release the
    sentinel so a later retry re-processes rather than 409-ing forever.
    """
    try:
        payload = json.dumps(jsonable_encoder(result))
    except (TypeError, ValueError) as exc:
        log_swallowed_error(_MODULE, exc, context={"key": key})
        _safe_delete(redis_client, key)
        return
    try:
        redis_client.set(key, payload, ex=_RESULT_TTL_S)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(_MODULE, exc, context={"key": key})
        _safe_delete(redis_client, key)
