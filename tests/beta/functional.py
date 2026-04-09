"""Comprehensive functional API tests for the Cerid AI MCP service.

Run inside Docker on the llm-network where the MCP service is reachable at
http://ai-companion-mcp:8888.

Priority markers:
    pytest -m p0   # critical path
    pytest -m p1   # important
    pytest -m p2   # nice-to-have
"""

from __future__ import annotations

import pathlib
import uuid

import httpx
import pytest

MCP_BASE_URL = "http://ai-companion-mcp:8888"
FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# P0 — Chat & Query
# ---------------------------------------------------------------------------


@pytest.mark.p0
def test_f01_agent_query(client: httpx.Client) -> None:
    """F-01: POST /agent/query returns an answer or results."""
    resp = client.post(
        "/agent/query",
        json={"query": "knowledge management", "top_k": 5},
        timeout=60.0,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data or "results" in data


@pytest.mark.p0
def test_f02_chat_streaming(client: httpx.Client) -> None:
    """F-02: POST /chat/stream returns SSE."""
    with client.stream(
        "POST",
        "/chat/stream",
        json={
            "model": "openrouter/auto",
            "messages": [
                {"role": "user", "content": "Hello, what can you help me with?"}
            ],
            "stream": True,
        },
        timeout=60.0,
    ) as resp:
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "text/event-stream" in ct
        # Read at least some bytes to confirm data flows
        chunk = next(resp.iter_bytes(chunk_size=64))
        assert len(chunk) > 0


@pytest.mark.p0
def test_f03_models_listing(client: httpx.Client) -> None:
    """F-03: GET /models/available returns a non-empty model list."""
    resp = client.get("/models/available")
    assert resp.status_code == 200
    data = resp.json()
    # Response may be a list or dict with models key
    if isinstance(data, dict):
        assert "models" in data and len(data["models"]) > 0
    else:
        assert isinstance(data, list) and len(data) > 0


# ---------------------------------------------------------------------------
# P0 — KB & Ingestion
# ---------------------------------------------------------------------------


@pytest.mark.p0
def test_f10_artifacts_list(client: httpx.Client) -> None:
    """F-10: GET /artifacts returns a list."""
    resp = client.get("/artifacts", params={"limit": 10})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.p0
def test_f11_taxonomy(client: httpx.Client) -> None:
    """F-11: GET /taxonomy has a 'domains' key."""
    resp = client.get("/taxonomy")
    assert resp.status_code == 200
    assert "domains" in resp.json()


@pytest.mark.p0
def test_f12_ingest_content(client: httpx.Client) -> None:
    """F-12: POST /ingest returns an artifact_id."""
    uid = uuid.uuid4().hex[:12]
    resp = client.post(
        "/ingest",
        json={
            "content": f"Beta test content {uid}",
            "domain": "general",
        },
    )
    assert resp.status_code == 200
    assert "artifact_id" in resp.json()


@pytest.mark.p0
def test_f13_file_upload(client: httpx.Client) -> None:
    """F-13: POST /upload with a text file succeeds."""
    sample = FIXTURES_DIR / "sample.txt"
    assert sample.exists(), f"Missing fixture: {sample}"
    # Use a separate client without the JSON content-type for multipart
    with httpx.Client(
        base_url=MCP_BASE_URL,
        headers={"X-Client-ID": "beta-test"},
        timeout=30.0,
    ) as upload_client:
        with open(sample, "rb") as f:
            resp = upload_client.post("/upload", files={"file": ("sample.txt", f, "text/plain")})
    assert resp.status_code == 200


@pytest.mark.p0
def test_f14_supported_formats(client: httpx.Client) -> None:
    """F-14: GET /upload/supported returns supported file extensions."""
    resp = client.get("/upload/supported")
    assert resp.status_code == 200
    data = resp.json()
    # Response is {count: N, extensions: [...]}
    if isinstance(data, dict):
        assert data.get("count", 0) > 0
        assert len(data.get("extensions", [])) > 0
    else:
        assert isinstance(data, list) and len(data) > 0


@pytest.mark.p0
def test_f15_ingest_log(client: httpx.Client) -> None:
    """F-15: GET /ingest_log returns a list."""
    resp = client.get("/ingest_log", params={"limit": 5})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# P1 — Settings & Config
# ---------------------------------------------------------------------------


@pytest.mark.p1
def test_f20_fetch_settings(client: httpx.Client) -> None:
    """F-20: GET /settings succeeds."""
    resp = client.get("/settings")
    assert resp.status_code == 200


@pytest.mark.p1
def test_f21_update_setting(client: httpx.Client) -> None:
    """F-21: PATCH /settings with a valid field succeeds."""
    resp = client.patch("/settings", json={"enable_feedback_loop": True})
    assert resp.status_code == 200


@pytest.mark.p1
def test_f22_setup_status(client: httpx.Client) -> None:
    """F-22: GET /setup/status succeeds."""
    resp = client.get("/setup/status")
    assert resp.status_code == 200


@pytest.mark.p1
def test_f23_provider_status(client: httpx.Client) -> None:
    """F-23: GET /providers succeeds."""
    resp = client.get("/providers")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# P1 — Monitoring & Maintenance
# ---------------------------------------------------------------------------


@pytest.mark.p1
def test_f30_maintenance(client: httpx.Client) -> None:
    """F-30: POST /agent/maintain with health mode succeeds."""
    resp = client.post(
        "/agent/maintain",
        json={"mode": "health"},
        timeout=60.0,
    )
    assert resp.status_code == 200


@pytest.mark.p1
def test_f31_rectify(client: httpx.Client) -> None:
    """F-31: POST /agent/rectify succeeds."""
    resp = client.post("/agent/rectify", json={"auto_fix": False}, timeout=60.0)
    assert resp.status_code == 200


@pytest.mark.p1
def test_f32_audit(client: httpx.Client) -> None:
    """F-32: POST /agent/audit with activity report succeeds."""
    resp = client.post(
        "/agent/audit",
        json={"report_type": "activity"},
        timeout=60.0,
    )
    assert resp.status_code == 200


@pytest.mark.p1
def test_f33_digest(client: httpx.Client) -> None:
    """F-33: GET /digest for last 24 hours succeeds."""
    resp = client.get("/digest", params={"hours": 24})
    assert resp.status_code == 200


@pytest.mark.p1
def test_f34_scheduler_status(client: httpx.Client) -> None:
    """F-34: GET /scheduler succeeds."""
    resp = client.get("/scheduler")
    assert resp.status_code == 200


@pytest.mark.p1
def test_f35_observability_metrics(client: httpx.Client) -> None:
    """F-35: GET /observability/metrics succeeds."""
    resp = client.get("/observability/metrics")
    assert resp.status_code == 200


@pytest.mark.p1
def test_f36_health_score(client: httpx.Client) -> None:
    """F-36: GET /observability/health-score succeeds."""
    resp = client.get("/observability/health-score")
    assert resp.status_code == 200


@pytest.mark.p1
def test_f37_plugins_list(client: httpx.Client) -> None:
    """F-37: GET /plugins succeeds."""
    resp = client.get("/plugins")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# P1 — Sync & Memories
# ---------------------------------------------------------------------------


@pytest.mark.p1
def test_f40_sync_status(client: httpx.Client) -> None:
    """F-40: GET /sync/status succeeds."""
    resp = client.get("/sync/status")
    assert resp.status_code == 200


@pytest.mark.p1
def test_f41_memory_list(client: httpx.Client) -> None:
    """F-41: GET /memories succeeds."""
    resp = client.get("/memories")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# P1 — Error Handling
# ---------------------------------------------------------------------------


@pytest.mark.p1
def test_f60_invalid_artifact(client: httpx.Client) -> None:
    """F-60: GET /artifacts/<bogus> returns 404."""
    resp = client.get("/artifacts/nonexistent-id-12345")
    assert resp.status_code == 404


@pytest.mark.p1
def test_f61_malformed_query(client: httpx.Client) -> None:
    """F-61: POST /agent/query with empty body returns 422."""
    resp = client.post("/agent/query", json={})
    assert resp.status_code == 422


@pytest.mark.p1
def test_f62_rate_limit_burst() -> None:
    """F-62: Rapid-fire requests trigger at least one 429."""
    with httpx.Client(
        base_url=MCP_BASE_URL,
        headers={
            "X-Client-ID": "unknown",
            "Content-Type": "application/json",
        },
        timeout=30.0,
    ) as burst_client:
        status_codes: list[int] = []
        for _ in range(15):
            resp = burst_client.post(
                "/agent/query",
                json={"query": "rate limit test", "top_k": 1},
            )
            status_codes.append(resp.status_code)

    assert 429 in status_codes, (
        f"Expected at least one 429 in {len(status_codes)} requests, "
        f"got: {set(status_codes)}"
    )
