# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""F084: a store size that was not measured is reported as null, not 0.

The report used to hardcode Neo4j at 0 MB and walk a Chroma directory that
does not exist in the API container, so the total was Redis + BM25 only
while reading as a full measurement.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from neo4j.exceptions import Neo4jError

from app.services import storage_metrics
from app.services.storage_metrics import get_storage_report

_MB = 1024 * 1024


def _redis(used_bytes: int = 0) -> MagicMock:
    r = MagicMock()
    r.get.return_value = None
    r.info.return_value = {"used_memory": used_bytes, "used_memory_peak": used_bytes}
    r.dbsize.return_value = 0
    return r


def _chroma(chunks: int = 0) -> MagicMock:
    client = MagicMock()
    coll = MagicMock()
    coll.count.return_value = chunks
    client.list_collections.return_value = [coll] if chunks else []
    return client


_NO_RECORD = object()


def _procedure_not_found() -> Exception:
    """What the deployed server (APOC Core only) answers, as the driver raises it."""
    return Neo4jError._hydrate_neo4j(
        code="Neo.ClientError.Procedure.ProcedureNotFound",
        message="There is no procedure with the name `apoc.monitor.store` registered for this database instance.",
    )


def _neo4j(*, nodes: int, store_bytes: object = None, store_error: Exception | None = None) -> MagicMock:
    """A driver whose session answers the count queries and the size call."""
    session = MagicMock()

    def run(query: str, *args, **kwargs):
        result = MagicMock()
        if "apoc.monitor.store" in query:
            if store_error is not None:
                raise store_error
            result.single.return_value = (
                None if store_bytes is _NO_RECORD else {"totalStoreSize": store_bytes}
            )
        else:
            result.single.return_value = {"c": nodes}
        return result

    session.run.side_effect = run
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    driver.session.return_value.__exit__.return_value = False
    return driver


@pytest.fixture(autouse=True)
def _no_local_stores(monkeypatch, tmp_path):
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "absent"))
    monkeypatch.setenv("NEO4J_DATA_DIR", str(tmp_path / "no-neo4j"))
    monkeypatch.setattr(storage_metrics, "BM25_DATA_DIR", str(tmp_path / "no-bm25"))
    monkeypatch.setattr(storage_metrics, "STORAGE_LIMIT_MB", 1000)


def _report(*, redis=None, chroma=None, neo4j=None) -> dict:
    redis = redis or _redis()
    return get_storage_report(
        use_cache=False,
        get_redis_fn=lambda: redis,
        get_chroma_fn=lambda: chroma or _chroma(),
        get_neo4j_fn=lambda: neo4j,
    )


def test_neo4j_size_comes_from_the_store():
    report = _report(neo4j=_neo4j(nodes=21950, store_bytes=50 * _MB))

    assert report["neo4j"]["disk_mb"] == 50.0
    assert report["neo4j"]["nodes"] == 21950
    assert report["total_mb"] == 50.0
    assert "neo4j" not in report["unmeasured"]


def test_neo4j_size_is_null_when_the_store_cannot_say():
    report = _report(neo4j=_neo4j(nodes=21950, store_error=RuntimeError("no such procedure")))

    assert report["neo4j"]["disk_mb"] is None
    assert report["neo4j"]["disk_mb_reason"]
    # The counts were measured and must survive the failed size call.
    assert report["neo4j"]["nodes"] == 21950
    assert "neo4j" in report["unmeasured"]


def test_neo4j_reason_names_the_missing_procedure_and_the_missing_mount(tmp_path):
    report = _report(neo4j=_neo4j(nodes=5, store_error=_procedure_not_found()))

    assert report["neo4j"]["disk_mb"] is None
    reason = report["neo4j"]["disk_mb_reason"]
    assert "not installed" in reason
    assert "APOC Extended" in reason
    assert str(tmp_path / "no-neo4j") in reason
    assert "not mounted" in reason


def test_neo4j_reason_carries_any_other_server_error():
    denied = Neo4jError._hydrate_neo4j(
        code="Neo.ClientError.Security.Forbidden",
        message="Executing procedure is not allowed",
    )
    report = _report(neo4j=_neo4j(nodes=5, store_error=denied))

    assert report["neo4j"]["disk_mb"] is None
    assert "Neo.ClientError.Security.Forbidden" in report["neo4j"]["disk_mb_reason"]


@pytest.mark.parametrize("answer", [_NO_RECORD, None, 0, "12"])
def test_neo4j_reason_says_when_the_procedure_answers_without_a_size(answer):
    report = _report(neo4j=_neo4j(nodes=5, store_bytes=answer))

    assert report["neo4j"]["disk_mb"] is None
    assert "returned no store size" in report["neo4j"]["disk_mb_reason"]
    assert report["neo4j"]["nodes"] == 5


def test_neo4j_size_is_measured_from_the_mounted_store_files(monkeypatch, tmp_path):
    data = tmp_path / "neo4j-data"
    (data / "databases" / "neo4j").mkdir(parents=True)
    (data / "databases" / "neo4j" / "neostore.nodestore.db").write_bytes(b"\0" * (3 * _MB))
    # Transaction logs are retention, not corpus: apoc's totalStoreSize
    # leaves them out and so does the walk.
    (data / "transactions" / "neo4j").mkdir(parents=True)
    (data / "transactions" / "neo4j" / "neostore.transaction.db.0").write_bytes(b"\0" * (5 * _MB))
    monkeypatch.setenv("NEO4J_DATA_DIR", str(data))
    driver = _neo4j(nodes=7, store_error=_procedure_not_found())

    report = _report(neo4j=driver)

    assert report["neo4j"]["disk_mb"] == 3.0
    assert "disk_mb_reason" not in report["neo4j"]
    assert report["neo4j"]["nodes"] == 7
    assert "neo4j" not in report["unmeasured"]
    session = driver.session.return_value.__enter__.return_value
    assert not any("apoc" in call.args[0] for call in session.run.call_args_list)


def test_neo4j_mount_with_no_readable_store_files_is_null(monkeypatch, tmp_path):
    data = tmp_path / "neo4j-data"
    (data / "databases").mkdir(parents=True)
    monkeypatch.setenv("NEO4J_DATA_DIR", str(data))

    report = _report(neo4j=_neo4j(nodes=7, store_error=_procedure_not_found()))

    assert report["neo4j"]["disk_mb"] is None
    assert "no readable store files" in report["neo4j"]["disk_mb_reason"]
    assert "not installed" in report["neo4j"]["disk_mb_reason"]


def test_disabled_neo4j_is_null_not_zero():
    report = _report(neo4j=None)

    assert report["neo4j"]["disk_mb"] is None
    assert report["neo4j"]["status"] == "disabled"


def test_chroma_size_is_null_when_the_directory_is_not_mounted():
    report = _report(chroma=_chroma(chunks=4802), neo4j=_neo4j(nodes=1, store_bytes=_MB))

    assert report["chromadb"]["disk_mb"] is None
    assert "not mounted" in report["chromadb"]["disk_mb_reason"]
    assert report["chromadb"]["chunks"] == 4802
    assert report["unmeasured"] == ["chromadb"]


def test_chroma_size_is_measured_when_the_directory_is_mounted(monkeypatch, tmp_path):
    mounted = tmp_path / "chroma"
    mounted.mkdir()
    (mounted / "data.bin").write_bytes(b"\0" * (2 * _MB))
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(mounted))

    report = _report(chroma=_chroma(chunks=10), neo4j=_neo4j(nodes=1, store_bytes=_MB))

    assert report["chromadb"]["disk_mb"] == 2.0
    assert report["unmeasured"] == []
    assert report["total_mb"] == 3.0


def test_both_mounted_stores_leave_nothing_unmeasured(monkeypatch, tmp_path):
    """The deployed shape: docker-compose.yml mounts both persist directories
    read-only into the API container, so the total is a full measurement."""
    neo4j_data = tmp_path / "neo4j-data"
    (neo4j_data / "databases" / "neo4j").mkdir(parents=True)
    (neo4j_data / "databases" / "neo4j" / "neostore.nodestore.db").write_bytes(b"\0" * (4 * _MB))
    chroma_data = tmp_path / "chroma-data"
    (chroma_data / "collection").mkdir(parents=True)
    (chroma_data / "chroma.sqlite3").write_bytes(b"\0" * (2 * _MB))
    (chroma_data / "collection" / "data_level0.bin").write_bytes(b"\0" * _MB)
    monkeypatch.setenv("NEO4J_DATA_DIR", str(neo4j_data))
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(chroma_data))

    report = _report(chroma=_chroma(chunks=10), neo4j=_neo4j(nodes=7, store_error=_procedure_not_found()))

    assert report["chromadb"]["disk_mb"] == 3.0
    assert report["neo4j"]["disk_mb"] == 4.0
    assert report["unmeasured"] == []
    assert report["total_mb"] == 7.0
    assert "disk_mb_reason" not in report["chromadb"]
    assert "disk_mb_reason" not in report["neo4j"]


def test_unmeasured_stores_do_not_change_the_backpressure_verdict():
    """The status ingest backpressure reads is the measured lower bound."""
    unreachable = RuntimeError("no such procedure")

    below = _report(redis=_redis(100 * _MB), neo4j=_neo4j(nodes=5, store_error=unreachable))
    assert below["total_mb"] == 100.0
    assert below["status"] == "healthy"

    above = _report(redis=_redis(900 * _MB), neo4j=_neo4j(nodes=5, store_error=unreachable))
    assert above["status"] == "critical"


def test_neo4j_transaction_log_size_is_reported_beside_the_store(monkeypatch, tmp_path):
    """Round 5 item 3: tx-log churn (8 x ~270 MB in five idle hours on
    2026-10-06) is invisible when only the store is measured."""
    data = tmp_path / "neo4j-data"
    (data / "databases" / "neo4j").mkdir(parents=True)
    (data / "databases" / "neo4j" / "neostore.nodestore.db").write_bytes(b"\0" * (3 * _MB))
    (data / "transactions" / "neo4j").mkdir(parents=True)
    (data / "transactions" / "neo4j" / "neostore.transaction.db.0").write_bytes(b"\0" * (5 * _MB))
    (data / "transactions" / "neo4j" / "neostore.transaction.db.1").write_bytes(b"\0" * (2 * _MB))
    monkeypatch.setenv("NEO4J_DATA_DIR", str(data))

    report = _report(neo4j=_neo4j(nodes=7, store_error=_procedure_not_found()))

    assert report["neo4j"]["disk_mb"] == 3.0
    assert report["neo4j"]["tx_log_mb"] == 7.0
    assert "tx_log_mb_reason" not in report["neo4j"]
    # Retention, not corpus: the total still counts the store alone.
    assert report["total_mb"] == 3.0


def test_neo4j_transaction_log_is_null_when_the_directory_is_not_mounted(monkeypatch, tmp_path):
    data = tmp_path / "neo4j-data"
    (data / "databases" / "neo4j").mkdir(parents=True)
    (data / "databases" / "neo4j" / "neostore.nodestore.db").write_bytes(b"\0" * (3 * _MB))
    monkeypatch.setenv("NEO4J_DATA_DIR", str(data))

    report = _report(neo4j=_neo4j(nodes=7, store_error=_procedure_not_found()))

    assert report["neo4j"]["tx_log_mb"] is None
    assert "transactions" in report["neo4j"]["tx_log_mb_reason"]
