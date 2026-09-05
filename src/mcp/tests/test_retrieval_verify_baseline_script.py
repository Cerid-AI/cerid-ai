"""The baseline harness must be importable and produce a JSON shape
even when every probe is stubbed. CI must not require Quenchforge or Docker."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# conftest inserts src/mcp on sys.path; repo-root scripts/ is a sibling of src/
# and must be visible for `scripts.smoke.*` (namespace package under scripts/).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def test_classify_503_no_slot_vs_backoff():
    from scripts.smoke.retrieval_verify_baseline import classify_qf_rerank_response

    no_slot = classify_qf_rerank_response(
        status=503, retry_after=None, body='{"error":"no rerank slot configured. Check `quenchforge doctor` for status."}'
    )
    assert no_slot == "no_slot"

    backoff = classify_qf_rerank_response(
        status=503, retry_after="2", body='{"error":"rerank slot is shedding load"}'
    )
    assert backoff == "backoff"

    ok = classify_qf_rerank_response(status=200, retry_after=None, body='{"results":[]}')
    assert ok == "ok"


def test_payload_schema_keys(tmp_path):
    from scripts.smoke.retrieval_verify_baseline import build_payload

    payload = build_payload(
        health={"version": "test", "status": "ok"},
        queue_depth={"kb": {"capacity": 4, "in_use": 0, "waiting": 0}},
        verification_rates={"today": {"timeout_rate": None, "claims_total": 0}},
        qf_rerank={"class": "no_slot", "status": 503},
        cgroup={"current_bytes": 1, "max_bytes": 6 * 1024**3},
        log_counts={"agent_query_exceeded": 0, "no_rerank_slot": 0},
        git_head="deadbeef",
    )
    required = {
        "timestamp", "git_head", "mcp_health", "queue_depth",
        "verification_rates", "qf_rerank", "cgroup", "log_counts",
    }
    assert required <= set(payload)
    out = tmp_path / "cap.json"
    out.write_text(json.dumps(payload))
    assert json.loads(out.read_text())["qf_rerank"]["class"] == "no_slot"
