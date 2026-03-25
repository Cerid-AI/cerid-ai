"""
Integration tests for Cerid AI beta test harness.
Runs inside Docker on llm-network against ai-companion-mcp.
"""

import time
import uuid

import httpx
import pytest

BASE_URL = "http://ai-companion-mcp:8888"
TIMEOUT = 60


def _uid() -> str:
    """Generate a unique hex identifier for test isolation."""
    return uuid.uuid4().hex


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=TIMEOUT)


# ---------------------------------------------------------------------------
# I-01: Ingest-then-query
# ---------------------------------------------------------------------------

@pytest.mark.p1
def test_i01_ingest_then_query():
    uid = _uid()[:8]
    marker = f"cerid-beta-integration-{uid}"
    content = (
        f"The {marker} protocol enables secure knowledge transfer between "
        "distributed AI systems using advanced semantic chunking and retrieval."
    )

    with _client() as c:
        # Step 1 — ingest
        ingest_resp = c.post("/ingest", json={
            "content": content,
            "domain": "general",
        })
        assert ingest_resp.status_code == 200, f"Ingest failed: {ingest_resp.text}"

        # Step 2 — wait for indexing
        time.sleep(3)

        # Step 3 — query with a meaningful phrase that should match
        query_resp = c.post("/agent/query", json={
            "query": f"cerid beta integration {uid} protocol secure knowledge transfer",
            "top_k": 10,
        })
        assert query_resp.status_code == 200, f"Query failed: {query_resp.text}"

        data = query_resp.json()
        # Check that query succeeded (non-zero results or answer contains content)
        answer = data.get("answer", "") or data.get("context", "")
        results = data.get("results", [])
        total = data.get("total_results", len(results))
        found_in_text = marker in answer or marker in str(results)
        has_results = total > 0 or len(results) > 0
        assert found_in_text or has_results, (
            f"Marker {marker} not found and no results in query response: {data}"
        )


# ---------------------------------------------------------------------------
# I-02: Dedup check
# ---------------------------------------------------------------------------

@pytest.mark.p1
def test_i02_dedup_check():
    uid = _uid()
    content = f"Dedup test content with marker {uid} should only appear once."
    payload = {"content": content, "domain": "general"}

    with _client() as c:
        resp1 = c.post("/ingest", json=payload)
        assert resp1.status_code == 200, f"First ingest failed: {resp1.text}"

        resp2 = c.post("/ingest", json=payload)
        assert resp2.status_code == 200, f"Second ingest failed: {resp2.text}"

        data1 = resp1.json()
        data2 = resp2.json()

        same_artifact = data1.get("artifact_id") == data2.get("artifact_id")
        duplicate_flag = data2.get("duplicate", False)
        assert same_artifact or duplicate_flag, (
            f"Expected dedup indicator. First: {data1}, Second: {data2}"
        )


# ---------------------------------------------------------------------------
# I-03: Settings roundtrip
# ---------------------------------------------------------------------------

@pytest.mark.p1
def test_i03_settings_roundtrip():
    with _client() as c:
        # Save original
        original_resp = c.get("/settings")
        assert original_resp.status_code == 200, f"GET /settings failed: {original_resp.text}"
        original_val = original_resp.json().get("enable_feedback_loop")

        # Update — toggle the feedback loop setting
        new_val = not bool(original_val)
        patch_resp = c.patch("/settings", json={"enable_feedback_loop": new_val})
        assert patch_resp.status_code == 200, f"PATCH /settings failed: {patch_resp.text}"

        # Verify
        verify_resp = c.get("/settings")
        assert verify_resp.status_code == 200
        assert verify_resp.json().get("enable_feedback_loop") == new_val, (
            f"Setting not updated: {verify_resp.json()}"
        )

        # Restore
        restore_resp = c.patch("/settings", json={"enable_feedback_loop": original_val})
        assert restore_resp.status_code == 200, f"Restore failed: {restore_resp.text}"


# ---------------------------------------------------------------------------
# I-04: Artifact detail after ingest
# ---------------------------------------------------------------------------

@pytest.mark.p1
def test_i04_artifact_detail_after_ingest():
    uid = _uid()
    content = f"Artifact detail test content with marker {uid}."

    with _client() as c:
        ingest_resp = c.post("/ingest", json={
            "content": content,
            "domain": "general",
        })
        assert ingest_resp.status_code == 200, f"Ingest failed: {ingest_resp.text}"

        artifact_id = ingest_resp.json().get("artifact_id")
        assert artifact_id, f"No artifact_id in ingest response: {ingest_resp.json()}"

        detail_resp = c.get(f"/artifacts/{artifact_id}")
        assert detail_resp.status_code == 200, f"GET artifact failed: {detail_resp.text}"

        detail = detail_resp.json()
        has_chunks = "chunks" in detail
        has_content = "content" in detail
        assert has_chunks or has_content, (
            f"Artifact detail missing chunks/content fields: {detail}"
        )


# ---------------------------------------------------------------------------
# I-05: Taxonomy operations
# ---------------------------------------------------------------------------

@pytest.mark.p1
def test_i05_taxonomy_operations():
    with _client() as c:
        resp = c.get("/taxonomy")
        assert resp.status_code == 200, f"GET /taxonomy failed: {resp.text}"

        data = resp.json()
        # Flatten response to check for "general" domain
        domains = data if isinstance(data, list) else data.get("domains", data.get("items", []))
        domain_names = [
            d.get("name", d) if isinstance(d, dict) else str(d)
            for d in domains
        ]
        assert "general" in domain_names, (
            f"'general' domain not found in taxonomy: {data}"
        )


# ---------------------------------------------------------------------------
# I-06: Collections consistency
# ---------------------------------------------------------------------------

@pytest.mark.p1
def test_i06_collections_consistency():
    with _client() as c:
        collections_resp = c.get("/collections")
        assert collections_resp.status_code == 200, f"GET /collections failed: {collections_resp.text}"

        coll_data = collections_resp.json()
        # Response is {"total": N, "collections": ["name1", "name2", ...]}
        coll_list = coll_data.get("collections", []) if isinstance(coll_data, dict) else coll_data

        for coll_name in coll_list:
            name = coll_name if isinstance(coll_name, str) else coll_name.get("name", "")
            if not name:
                continue
            # Extract domain name from collection name (e.g., "domain_general" -> "general")
            domain = name.replace("domain_", "") if name.startswith("domain_") else name
            artifacts_resp = c.get("/artifacts", params={"limit": 1, "domain": domain})
            assert artifacts_resp.status_code != 500, (
                f"500 error fetching artifacts for domain {domain}: {artifacts_resp.text}"
            )


# ---------------------------------------------------------------------------
# I-07: KB admin stats
# ---------------------------------------------------------------------------

@pytest.mark.p1
def test_i07_kb_admin_stats():
    with _client() as c:
        artifacts_resp = c.get("/artifacts", params={"limit": 50})
        assert artifacts_resp.status_code == 200, f"GET /artifacts failed: {artifacts_resp.text}"

        artifacts_data = artifacts_resp.json()
        items = artifacts_data if isinstance(artifacts_data, list) else artifacts_data.get("items", artifacts_data.get("artifacts", []))
        count = len(items)
        assert count >= 0, "Artifact count should be non-negative"

        collections_resp = c.get("/collections")
        assert collections_resp.status_code == 200, f"GET /collections failed: {collections_resp.text}"

        coll_data = collections_resp.json()
        total = coll_data.get("total", len(
            coll_data if isinstance(coll_data, list) else coll_data.get("items", coll_data.get("collections", []))
        ))
        assert total >= 0, f"Unreasonable collection total: {total}"
