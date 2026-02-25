"""Database connection dependencies — lazy singletons shared across routers."""
from __future__ import annotations

import logging
import threading

import chromadb
import redis
from chromadb.config import Settings as ChromaSettings
from neo4j import GraphDatabase

import config

logger = logging.getLogger("ai-companion")

_chroma = None
_redis = None
_neo4j = None
_init_lock = threading.Lock()


def get_chroma() -> chromadb.HttpClient:
    global _chroma
    if _chroma is None:
        with _init_lock:
            if _chroma is None:
                host = config.CHROMA_URL.replace("http://", "").split(":")[0]
                port = int(config.CHROMA_URL.split(":")[-1])
                _chroma = chromadb.HttpClient(
                    host=host, port=port, settings=ChromaSettings(anonymized_telemetry=False)
                )
                _chroma.heartbeat()
                logger.info("ChromaDB connected")
    return _chroma


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        with _init_lock:
            if _redis is None:
                _redis = redis.from_url(
                    config.REDIS_URL, decode_responses=True, socket_connect_timeout=5
                )
                _redis.ping()
                logger.info("Redis connected")
    return _redis


def get_neo4j():
    global _neo4j
    if _neo4j is None:
        with _init_lock:
            if _neo4j is None:
                _neo4j = GraphDatabase.driver(
                    config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
                )
                _neo4j.verify_connectivity()
                logger.info("Neo4j connected")
    return _neo4j


def close_neo4j():
    """Called from main.py lifespan on shutdown."""
    global _neo4j
    if _neo4j:
        _neo4j.close()
        _neo4j = None
        logger.info("Neo4j connection closed")
