# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed Redis wrapper for sync redis.Redis with decode_responses=True.

The redis-py type stubs define method return types as ``Awaitable[T] | T``
because the same mixin classes serve both sync and async clients.  When
using a **sync** ``redis.Redis`` client, every call returns the concrete
``T`` — but mypy can't narrow this automatically.

This thin wrapper delegates every call to the underlying sync client and
provides properly narrowed return types, eliminating ~57 mypy errors in
one place instead of scattering ``cast()`` across 19 files.

Usage::

    from utils.typed_redis import TypedRedis

    r = TypedRedis(redis.from_url(..., decode_responses=True))
    val: str | None = r.get("key")   # mypy sees str | None, not Awaitable|Any
"""
from __future__ import annotations

import builtins
from collections.abc import Mapping
from typing import Any, Iterator

import redis as _redis_lib

# Stream entry: (entry_id, field_dict)
StreamEntry = tuple[str, dict[str, str]]

# Flexible field dict accepted by xadd — Mapping is covariant so
# callers can pass dict[str, str] without mypy complaining.
FieldMap = Mapping[str, str | int | float | bytes]


class TypedRedis:
    """Typed facade over a sync ``redis.Redis[str]`` client.

    Only methods actually used in the codebase are typed here.
    Any unlisted attribute falls through to the underlying client via
    ``__getattr__``, preserving full backward compatibility.
    """

    __slots__ = ("_r",)

    def __init__(self, client: _redis_lib.Redis) -> None:  # type: ignore[type-arg]
        self._r = client

    # -- passthrough for unlisted methods / attributes ----------------------
    def __getattr__(self, name: str) -> Any:
        return getattr(self._r, name)

    # -- String commands ----------------------------------------------------
    def get(self, name: str) -> str | None:
        return self._r.get(name)  # type: ignore[return-value]

    def set(
        self,
        name: str,
        value: str | int | float,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
        xx: bool = False,
        keepttl: bool = False,
    ) -> bool:
        return self._r.set(name, value, ex=ex, px=px, nx=nx, xx=xx, keepttl=keepttl)  # type: ignore[return-value]

    def delete(self, *names: str) -> int:
        return self._r.delete(*names)  # type: ignore[return-value]

    def exists(self, *names: str) -> int:
        return self._r.exists(*names)  # type: ignore[return-value]

    def keys(self, pattern: str = "*") -> list[str]:
        return self._r.keys(pattern)  # type: ignore[return-value]

    def mget(self, keys: str | list[str], *more: str) -> list[str | None]:
        # Accept both mget("a", "b") and mget(["a", "b"])
        if isinstance(keys, list):
            return self._r.mget(keys)  # type: ignore[return-value]
        return self._r.mget(keys, *more)  # type: ignore[return-value]

    def scan(
        self,
        cursor: int = 0,
        match: str | None = None,
        count: int | None = None,
    ) -> tuple[int, list[str]]:
        return self._r.scan(cursor=cursor, match=match, count=count)  # type: ignore[return-value]

    def scan_iter(self, match: str | None = None, count: int | None = None) -> Iterator[str]:
        return self._r.scan_iter(match=match, count=count)  # type: ignore[return-value]

    # -- Numeric commands ---------------------------------------------------
    def incr(self, name: str, amount: int = 1) -> int:
        return self._r.incr(name, amount)  # type: ignore[return-value]

    def incrby(self, name: str, amount: int = 1) -> int:
        return self._r.incrby(name, amount)  # type: ignore[return-value]

    def expire(self, name: str, time: int) -> bool:
        return self._r.expire(name, time)  # type: ignore[return-value]

    # -- Hash commands ------------------------------------------------------
    def hset(self, name: str, key: str | None = None, value: str | None = None, mapping: dict[str, str] | None = None) -> int:
        return self._r.hset(name, key=key, value=value, mapping=mapping)  # type: ignore[arg-type,return-value]

    def hget(self, name: str, key: str) -> str | None:
        return self._r.hget(name, key)  # type: ignore[return-value]

    def hgetall(self, name: str) -> dict[str, str]:
        return self._r.hgetall(name)  # type: ignore[return-value]

    def hdel(self, name: str, *keys: str) -> int:
        return self._r.hdel(name, *keys)  # type: ignore[return-value]

    # -- Set commands -------------------------------------------------------
    def smembers(self, name: str) -> builtins.set[str]:
        return self._r.smembers(name)  # type: ignore[return-value]

    def sadd(self, name: str, *values: str) -> int:
        return self._r.sadd(name, *values)  # type: ignore[return-value]

    def srem(self, name: str, *values: str) -> int:
        return self._r.srem(name, *values)  # type: ignore[return-value]

    def scard(self, name: str) -> int:
        return self._r.scard(name)  # type: ignore[return-value]

    def sismember(self, name: str, value: str) -> bool:
        return self._r.sismember(name, value)  # type: ignore[return-value]

    # -- List commands ------------------------------------------------------
    def lpush(self, name: str, *values: str) -> int:
        return self._r.lpush(name, *values)  # type: ignore[return-value]

    def rpush(self, name: str, *values: str) -> int:
        return self._r.rpush(name, *values)  # type: ignore[return-value]

    def lrange(self, name: str, start: int, end: int) -> list[str]:
        return self._r.lrange(name, start, end)  # type: ignore[return-value]

    def llen(self, name: str) -> int:
        return self._r.llen(name)  # type: ignore[return-value]

    # -- Stream commands ----------------------------------------------------
    def xadd(self, name: str, fields: FieldMap, id: str = "*", maxlen: int | None = None) -> str:
        return self._r.xadd(name, fields, id=id, maxlen=maxlen)  # type: ignore[arg-type,return-value]

    def xrange(
        self,
        name: str,
        min: str = "-",
        max: str = "+",
        count: int | None = None,
    ) -> list[StreamEntry]:
        return self._r.xrange(name, min=min, max=max, count=count)  # type: ignore[return-value]

    def xrevrange(
        self,
        name: str,
        max: str = "+",
        min: str = "-",
        count: int | None = None,
    ) -> list[StreamEntry]:
        return self._r.xrevrange(name, max=max, min=min, count=count)  # type: ignore[return-value]

    def xlen(self, name: str) -> int:
        return self._r.xlen(name)  # type: ignore[return-value]

    def xdel(self, name: str, *ids: str) -> int:
        return self._r.xdel(name, *ids)  # type: ignore[return-value]

    def xtrim(self, name: str, maxlen: int | None = None, approximate: bool = True, minid: str | None = None) -> int:
        return self._r.xtrim(name, maxlen=maxlen, approximate=approximate, minid=minid)  # type: ignore[return-value]

    # -- Pub/Sub & Pipeline -------------------------------------------------
    def pubsub(self) -> Any:
        return self._r.pubsub()

    def pipeline(self, transaction: bool = True) -> Any:
        return self._r.pipeline(transaction=transaction)

    def publish(self, channel: str, message: str) -> int:
        return self._r.publish(channel, message)  # type: ignore[return-value]

    # -- Connection ---------------------------------------------------------
    def ping(self) -> bool:
        return self._r.ping()  # type: ignore[return-value]

    def close(self) -> None:
        self._r.close()
