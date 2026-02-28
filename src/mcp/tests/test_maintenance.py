# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for agents/maintenance.py — system health checks and automated cleanup."""

from unittest.mock import MagicMock, patch

from agents.maintenance import (
    _check_bifrost_sync,
    analyze_collections,
    check_system_health,
    purge_artifacts,
)

# ---------------------------------------------------------------------------
# Tests: check_system_health
# ---------------------------------------------------------------------------

class TestCheckSystemHealth:
    @patch("agents.maintenance.config")
    def test_all_services_healthy(self, mock_config, mock_neo4j, mock_chroma, mock_redis):
        mock_config.REDIS_INGEST_LOG = "ingest:log"
        driver, session = mock_neo4j
        client, collection = mock_chroma
        redis = mock_redis

        # ChromaDB healthy
        col_obj = MagicMock()
        col_obj.name = "domain_coding"
        col_obj.count.return_value = 50
        client.list_collections.return_value = [col_obj]

        # Neo4j healthy
        record1 = {"artifact_count": 10}
        record2 = {"domain_count": 3}
        session.run.return_value.single.side_effect = [record1, record2]

        # Redis healthy
        redis.ping.return_value = True
        redis.llen.return_value = 100

        health = check_system_health(driver, client, redis)
        assert health["services"]["chromadb"] == "connected"
        assert health["services"]["neo4j"] == "connected"
        assert health["services"]["redis"] == "connected"
        assert health["data"]["collections"] == 1
        assert health["data"]["total_chunks"] == 50
        assert health["data"]["artifacts"] == 10

    @patch("agents.maintenance.config")
    def test_chromadb_error(self, mock_config, mock_neo4j, mock_redis):
        driver, session = mock_neo4j
        redis = mock_redis

        chroma = MagicMock()
        chroma.heartbeat.side_effect = Exception("Connection refused")

        session.run.return_value.single.side_effect = [{"artifact_count": 0}, {"domain_count": 0}]
        redis.ping.return_value = True
        redis.llen.return_value = 0
        mock_config.REDIS_INGEST_LOG = "ingest:log"

        health = check_system_health(driver, chroma, redis)
        assert "error" in health["services"]["chromadb"]
        assert health["overall"] == "degraded"

    @patch("agents.maintenance.config")
    def test_neo4j_error(self, mock_config, mock_chroma, mock_redis):
        mock_config.REDIS_INGEST_LOG = "ingest:log"
        client, collection = mock_chroma
        redis = mock_redis

        driver = MagicMock()
        driver.session.side_effect = Exception("Neo4j down")

        client.list_collections.return_value = []
        redis.ping.return_value = True
        redis.llen.return_value = 0

        health = check_system_health(driver, client, redis)
        assert "error" in health["services"]["neo4j"]
        assert health["overall"] == "degraded"

    @patch("agents.maintenance.config")
    def test_redis_error(self, mock_config, mock_neo4j, mock_chroma):
        mock_config.REDIS_INGEST_LOG = "ingest:log"
        driver, session = mock_neo4j
        client, collection = mock_chroma

        redis = MagicMock()
        redis.ping.side_effect = Exception("Redis down")

        client.list_collections.return_value = []
        session.run.return_value.single.side_effect = [{"artifact_count": 0}, {"domain_count": 0}]

        health = check_system_health(driver, client, redis)
        assert "error" in health["services"]["redis"]
        assert health["overall"] == "degraded"

    @patch("agents.maintenance.config")
    def test_has_timestamp(self, mock_config, mock_neo4j, mock_chroma, mock_redis):
        mock_config.REDIS_INGEST_LOG = "ingest:log"
        driver, session = mock_neo4j
        client, collection = mock_chroma
        redis = mock_redis

        client.list_collections.return_value = []
        session.run.return_value.single.side_effect = [{"artifact_count": 0}, {"domain_count": 0}]
        redis.ping.return_value = True
        redis.llen.return_value = 0

        health = check_system_health(driver, client, redis)
        assert "timestamp" in health

    @patch("agents.maintenance.config")
    def test_collection_sizes_tracked(self, mock_config, mock_neo4j, mock_chroma, mock_redis):
        mock_config.REDIS_INGEST_LOG = "ingest:log"
        driver, session = mock_neo4j
        client, collection = mock_chroma
        redis = mock_redis

        col1 = MagicMock()
        col1.name = "domain_coding"
        col1.count.return_value = 100
        col2 = MagicMock()
        col2.name = "domain_finance"
        col2.count.return_value = 50
        client.list_collections.return_value = [col1, col2]

        session.run.return_value.single.side_effect = [{"artifact_count": 0}, {"domain_count": 0}]
        redis.ping.return_value = True
        redis.llen.return_value = 0

        health = check_system_health(driver, client, redis)
        assert health["data"]["collection_sizes"]["domain_coding"] == 100
        assert health["data"]["collection_sizes"]["domain_finance"] == 50
        assert health["data"]["total_chunks"] == 150


# ---------------------------------------------------------------------------
# Tests: _check_bifrost_sync
# ---------------------------------------------------------------------------

class TestCheckBifrostSync:
    @patch("agents.maintenance.config")
    @patch("urllib.request.urlopen")
    def test_healthy_bifrost(self, mock_urlopen, mock_config):
        mock_config.BIFROST_URL = "http://bifrost:8080/v1"
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        assert _check_bifrost_sync() == "connected"

    @patch("agents.maintenance.config")
    @patch("urllib.request.urlopen")
    def test_bifrost_non_200(self, mock_urlopen, mock_config):
        mock_config.BIFROST_URL = "http://bifrost:8080/v1"
        mock_resp = MagicMock()
        mock_resp.status = 503
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = _check_bifrost_sync()
        assert "503" in result

    @patch("agents.maintenance.config")
    @patch("urllib.request.urlopen")
    def test_bifrost_unreachable(self, mock_urlopen, mock_config):
        mock_config.BIFROST_URL = "http://bifrost:8080/v1"
        mock_urlopen.side_effect = Exception("Connection refused")

        result = _check_bifrost_sync()
        assert "unreachable" in result


# ---------------------------------------------------------------------------
# Tests: purge_artifacts
# ---------------------------------------------------------------------------

class TestPurgeArtifacts:
    @patch("agents.maintenance.config")
    def test_purge_single_artifact(self, mock_config, mock_neo4j, mock_chroma):
        mock_config.collection_name = lambda d: f"domain_{d}"
        driver, session = mock_neo4j
        client, collection = mock_chroma

        record = MagicMock()
        record.__getitem__ = lambda s, k: {
            "id": "art-1", "filename": "old.py", "domain": "coding",
            "chunk_ids": '["c1", "c2"]',
        }[k]
        record.get = lambda k, default=None: {
            "id": "art-1", "filename": "old.py", "domain": "coding",
            "chunk_ids": '["c1", "c2"]',
        }.get(k, default)
        session.run.return_value.single.return_value = record

        result = purge_artifacts(driver, client, ["art-1"])
        assert result["purged_count"] == 1
        assert result["error_count"] == 0
        assert result["purged"][0]["id"] == "art-1"
        assert result["purged"][0]["chunks_removed"] == 2

    @patch("agents.maintenance.config")
    def test_artifact_not_found(self, mock_config, mock_neo4j, mock_chroma):
        driver, session = mock_neo4j
        client, collection = mock_chroma
        session.run.return_value.single.return_value = None

        result = purge_artifacts(driver, client, ["nonexistent"])
        assert result["purged_count"] == 0
        assert result["error_count"] == 1
        assert result["errors"][0]["error"] == "not found"

    @patch("agents.maintenance.config")
    def test_partial_error(self, mock_config, mock_neo4j, mock_chroma):
        mock_config.collection_name = lambda d: f"domain_{d}"
        driver, session = mock_neo4j
        client, collection = mock_chroma

        # First artifact: succeeds
        record_ok = MagicMock()
        record_ok.__getitem__ = lambda s, k: {
            "id": "a1", "filename": "ok.py", "domain": "coding", "chunk_ids": "[]",
        }[k]
        record_ok.get = lambda k, default=None: {
            "id": "a1", "filename": "ok.py", "domain": "coding", "chunk_ids": "[]",
        }.get(k, default)

        # Second artifact: driver error
        session.run.return_value.single.side_effect = [record_ok, Exception("DB error")]

        result = purge_artifacts(driver, client, ["a1", "a2"])
        assert result["purged_count"] == 1
        assert result["error_count"] == 1

    @patch("agents.maintenance.log_event")
    @patch("agents.maintenance.config")
    def test_logs_purge_to_redis(self, mock_config, mock_log, mock_neo4j, mock_chroma):
        mock_config.collection_name = lambda d: f"domain_{d}"
        driver, session = mock_neo4j
        client, collection = mock_chroma
        redis = MagicMock()

        record = MagicMock()
        record.__getitem__ = lambda s, k: {
            "id": "a1", "filename": "f.py", "domain": "coding", "chunk_ids": "[]",
        }[k]
        record.get = lambda k, default=None: {
            "id": "a1", "filename": "f.py", "domain": "coding", "chunk_ids": "[]",
        }.get(k, default)
        session.run.return_value.single.return_value = record

        purge_artifacts(driver, client, ["a1"], redis_client=redis)
        mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: analyze_collections
# ---------------------------------------------------------------------------

class TestAnalyzeCollections:
    @patch("agents.maintenance.config")
    def test_basic_analysis(self, mock_config):
        mock_config.DOMAINS = ["coding", "general"]
        mock_config.collection_name = lambda d: f"domain_{d}"

        col1 = MagicMock()
        col1.name = "domain_coding"
        col1.count.return_value = 100
        col2 = MagicMock()
        col2.name = "domain_general"
        col2.count.return_value = 50

        client = MagicMock()
        client.list_collections.return_value = [col1, col2]

        result = analyze_collections(client)
        assert result["total_chunks"] == 150
        assert result["collections"]["domain_coding"]["chunks"] == 100
        assert result["empty_collections"] == []
        assert result["missing_collections"] == []

    @patch("agents.maintenance.config")
    def test_detects_empty_collections(self, mock_config):
        mock_config.DOMAINS = ["coding"]
        mock_config.collection_name = lambda d: f"domain_{d}"

        col = MagicMock()
        col.name = "domain_coding"
        col.count.return_value = 0

        client = MagicMock()
        client.list_collections.return_value = [col]

        result = analyze_collections(client)
        assert "domain_coding" in result["empty_collections"]
        assert len(result["recommendations"]) > 0

    @patch("agents.maintenance.config")
    def test_detects_missing_collections(self, mock_config):
        mock_config.DOMAINS = ["coding", "finance"]
        mock_config.collection_name = lambda d: f"domain_{d}"

        col = MagicMock()
        col.name = "domain_coding"
        col.count.return_value = 10

        client = MagicMock()
        client.list_collections.return_value = [col]

        result = analyze_collections(client)
        assert "domain_finance" in result["missing_collections"]

    @patch("agents.maintenance.config")
    def test_detects_extra_collections(self, mock_config):
        mock_config.DOMAINS = ["coding"]
        mock_config.collection_name = lambda d: f"domain_{d}"

        col1 = MagicMock()
        col1.name = "domain_coding"
        col1.count.return_value = 10
        col2 = MagicMock()
        col2.name = "domain_legacy"
        col2.count.return_value = 5

        client = MagicMock()
        client.list_collections.return_value = [col1, col2]

        result = analyze_collections(client)
        assert "domain_legacy" in result["extra_collections"]

    @patch("agents.maintenance.config")
    def test_no_collections(self, mock_config):
        mock_config.DOMAINS = ["coding"]
        mock_config.collection_name = lambda d: f"domain_{d}"

        client = MagicMock()
        client.list_collections.return_value = []

        result = analyze_collections(client)
        assert result["total_chunks"] == 0
        assert "domain_coding" in result["missing_collections"]
