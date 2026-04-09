# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Database connection dependencies — lazy singletons shared across routers."""
from __future__ import annotations

import logging
import threading
import time as _time
from typing import Any
from urllib.parse import urlparse

import chromadb
import redis
from chromadb.config import Settings as ChromaSettings
from neo4j import GraphDatabase

import config

logger = logging.getLogger("ai-companion")


def parse_chroma_url(url: str | None = None) -> tuple[str, int]:
    """Parse a ChromaDB URL into (host, port). Supports http:// and https://."""
    url = url or config.CHROMA_URL
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8000
    return host, port


def _retry(fn, label: str, attempts: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
    """Retry a connectivity check with exponential backoff and jitter."""
    import random

    for attempt in range(1, attempts + 1):
        try:
            fn()
            return
        except Exception as exc:
            if attempt == attempts:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            jittered = random.uniform(0, delay)
            logger.warning(
                "%s connectivity check failed (attempt %d/%d): %s — retrying in %.1fs",
                label, attempt, attempts, exc, jittered,
            )
            _time.sleep(jittered)


_chroma = None
_redis = None
_neo4j = None
_chroma_lock = threading.Lock()
_redis_lock = threading.Lock()
_neo4j_lock = threading.Lock()


class _EmbeddingAwareClient:
    """Thin proxy that injects the configured embedding function automatically.

    When ``EMBEDDING_MODEL`` differs from ChromaDB's built-in default,
    the client-side ONNX embedding function is passed to every
    ``get_or_create_collection`` / ``get_collection`` call so callers
    don't need any changes.
    """

    __slots__ = ("_client", "_ef")

    def __init__(self, client: Any) -> None:
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_ef", None)

    def _embedding_function(self) -> Any:
        ef = object.__getattribute__(self, "_ef")
        if ef is not None:
            return ef
        from utils.embeddings import get_embedding_function
        ef = get_embedding_function()
        object.__setattr__(self, "_ef", ef)
        return ef

    def get_or_create_collection(self, **kwargs: Any) -> Any:
        ef = self._embedding_function()
        if ef is not None and "embedding_function" not in kwargs:
            kwargs["embedding_function"] = ef
        return object.__getattribute__(self, "_client").get_or_create_collection(**kwargs)

    def get_collection(self, **kwargs: Any) -> Any:
        ef = self._embedding_function()
        if ef is not None and "embedding_function" not in kwargs:
            kwargs["embedding_function"] = ef
        return object.__getattribute__(self, "_client").get_collection(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_client"), name)


def get_chroma() -> Any:
    global _chroma
    if _chroma is None:
        with _chroma_lock:
            if _chroma is None:
                host, port = parse_chroma_url()
                raw = chromadb.HttpClient(
                    host=host, port=port, settings=ChromaSettings(anonymized_telemetry=False)
                )
                _retry(raw.heartbeat, "ChromaDB")
                _chroma = _EmbeddingAwareClient(raw)
                logger.info("ChromaDB connected")
    return _chroma


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        with _redis_lock:
            if _redis is None:
                _redis = redis.from_url(
                    config.REDIS_URL, decode_responses=True,
                    socket_connect_timeout=5, socket_timeout=10,
                    max_connections=20,
                )
                _retry(_redis.ping, "Redis")
                logger.info("Redis connected")
    return _redis


def get_neo4j():
    global _neo4j
    if _neo4j is None:
        with _neo4j_lock:
            if _neo4j is None:
                if not config.NEO4J_PASSWORD:
                    raise RuntimeError(
                        "NEO4J_PASSWORD is empty — check .env file and docker-compose env_file"
                    )
                driver = GraphDatabase.driver(
                    config.NEO4J_URI,
                    auth=(config.NEO4J_USER, config.NEO4J_PASSWORD),
                    max_connection_pool_size=25,
                    connection_acquisition_timeout=30,
                )
                # verify_connectivity only checks transport, not auth.
                # Run a simple query to validate credentials.
                try:
                    def _verify_neo4j_auth():
                        with driver.session() as s:
                            s.run("RETURN 1").consume()
                    _retry(_verify_neo4j_auth, "Neo4j")
                except Exception:
                    driver.close()
                    raise
                _neo4j = driver
                logger.info("Neo4j connected (auth verified)")
    return _neo4j


def close_neo4j():
    """Called from main.py lifespan on shutdown."""
    global _neo4j
    if _neo4j:
        _neo4j.close()
        _neo4j = None
        logger.info("Neo4j connection closed")


def close_chroma():
    """Called from main.py lifespan on shutdown."""
    global _chroma
    if _chroma:
        # HttpClient has no explicit close — clear reference for GC
        _chroma = None
        logger.info("ChromaDB client released")


def close_redis():
    """Called from main.py lifespan on shutdown."""
    global _redis
    if _redis:
        try:
            _redis.close()
        except Exception as e:
            logger.debug(f"Redis close error (ignored): {e}")
        _redis = None
        logger.info("Redis connection closed")
