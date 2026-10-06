# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""What docker-compose.yml promises /system/storage about the data stores.

``app/services/storage_metrics.py`` can only measure a store whose files are
readable from the API container: it walks ``NEO4J_DATA_DIR`` and
``CHROMA_PERSIST_DIR`` and reports the store as unmeasured otherwise. The
Chroma client has no size call at all, so until the persist directory was
mounted the report listed chromadb as unmeasured on every deployment.

Each assertion reads the service definitions, so a renamed source directory
or a dropped env line fails here rather than as a silent ``unmeasured`` entry
on the live report.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent.parent


def _services() -> dict:
    return yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]


def _mounts(service: dict) -> dict[str, tuple[str, bool]]:
    """``{container_path: (host_source, read_only)}`` from short-form volumes."""
    mounts: dict[str, tuple[str, bool]] = {}
    for spec in service.get("volumes", []):
        # Split from the right: a source such as ${A:-${B:-x}} carries colons.
        parts = str(spec).split(":")
        if parts[-1] in {"ro", "rw", "cached", "delegated", "ro,cached", "rw,cached"}:
            mode, target, source = parts[-1], parts[-2], ":".join(parts[:-2])
        else:
            mode, target, source = "", parts[-1], ":".join(parts[:-1])
        mounts[target] = (source, "ro" in mode.split(","))
    return mounts


def _env(service: dict) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in service.get("environment", []):
        key, _, value = str(line).partition("=")
        env[key] = value
    return env


def test_chroma_persist_directory_is_mounted_read_only_for_measurement() -> None:
    services = _services()
    chroma_source, _ = _mounts(services["chromadb"])["/data"]
    api_mounts = _mounts(services["mcp-server"])

    assert "/chroma-data" in api_mounts, "mcp-server does not mount the Chroma persist directory"
    source, read_only = api_mounts["/chroma-data"]
    assert source == chroma_source, "mcp-server mounts a different directory than chromadb persists to"
    assert read_only, "the API container must not be able to write Chroma's files"
    assert _env(services["mcp-server"]).get("CHROMA_PERSIST_DIR") == "/chroma-data"


def test_neo4j_store_is_mounted_read_only_for_measurement() -> None:
    services = _services()
    neo4j_source, _ = _mounts(services["neo4j"])["/data"]
    api_mounts = _mounts(services["mcp-server"])

    assert "/neo4j-data" in api_mounts
    source, read_only = api_mounts["/neo4j-data"]
    assert source == neo4j_source
    assert read_only


def test_neo4j_transaction_log_retention_is_bounded_and_identical_in_both_stacks() -> None:
    """Neo4j's default keeps 2 days or 2G of transaction logs per database;
    the live host carried 2.5 GB of logs against a 461 MB store. Both compose
    files mount the same store, so they must agree on the bound."""
    key = "NEO4J_db_tx__log_rotation_retention__policy"
    root = _env(_services()["neo4j"]).get(key)
    legacy = yaml.safe_load((REPO / "stacks" / "infrastructure" / "docker-compose.yml").read_text())
    infra = _env(legacy["services"]["neo4j"]).get(key)

    assert root, f"docker-compose.yml does not set {key}"
    assert infra, f"stacks/infrastructure/docker-compose.yml does not set {key}"
    assert root == infra, f"retention policies differ: {root!r} vs {infra!r}"
    assert root not in {"true", "keep_all"}


def test_redis_image_is_pinned_identically_in_every_place_that_runs_it() -> None:
    """Three places name the Redis image: both compose files start the store
    from it, and ``scripts/lib/healthcheck.sh`` runs ``redis-check-aof`` from
    it against the store's AOF before the stack starts. Dependabot watches
    only the compose files, so the healthcheck pin falls behind silently and
    an AOF written by a newer server is then checked by an older tool."""
    image = re.compile(r"redis:\d[\w.\-]*")
    root = _services()["redis"]["image"]
    legacy = yaml.safe_load((REPO / "stacks" / "infrastructure" / "docker-compose.yml").read_text())
    infra = legacy["services"]["redis"]["image"]
    healthcheck = (REPO / "scripts" / "lib" / "healthcheck.sh").read_text()
    match = re.search(r'redis_image="(' + image.pattern + r')"', healthcheck)

    assert match, "healthcheck.sh no longer pins redis_image"
    assert image.fullmatch(root), root
    assert root == infra, f"compose files disagree on the redis image: {root!r} vs {infra!r}"
    assert root == match.group(1), f"healthcheck.sh pins {match.group(1)!r}, compose pins {root!r}"
