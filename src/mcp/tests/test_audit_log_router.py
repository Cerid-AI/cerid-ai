# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the /audit-log REST surface (Enterprise ``audit_logging``).

Two things are pinned that a 200 alone would not show:

* the **differential** — Enterprise serves it, community is refused. A gate is
  only demonstrated by the refusal; a paid endpoint that returns 200 on every
  tier looks exactly like a working one until someone checks the other tier.
* that a **tampered log verifies as tampered through the endpoint**, not just
  in the module. The endpoint is what an auditor actually calls.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.utils import audit_log


def _make_app() -> FastAPI:
    from app.routers.audit_log import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(audit_log, "_write_failures", 0, raising=False)
    yield


@pytest.fixture(autouse=True)
def _enterprise_tier():
    from config.features import FEATURE_TIER, set_tier

    original = FEATURE_TIER
    set_tier("enterprise")
    try:
        yield
    finally:
        set_tier(original)


@pytest.fixture()
def client():
    return TestClient(_make_app())


class TestGate:
    PATHS = ["/audit-log", "/audit-log/verify"]

    def test_community_is_refused(self, client):
        from config.features import set_tier

        set_tier("community")
        for path in self.PATHS:
            resp = client.get(path)
            assert resp.status_code == 403, f"{path} should be Enterprise-gated"

    def test_pro_is_refused(self, client):
        # Enterprise-only, not merely paid. Until 2026-08-11 this flag had no
        # gate at all, so the tier boundary is the thing worth asserting.
        from config.features import set_tier

        set_tier("pro")
        for path in self.PATHS:
            assert client.get(path).status_code == 403

    def test_enterprise_is_allowed(self, client):
        for path in self.PATHS:
            assert client.get(path).status_code == 200

    def test_refuses_when_the_gate_cannot_be_evaluated(self, client, monkeypatch):
        # Fail CLOSED. `except ImportError: pass` around a feature check shipped
        # in this repo once and silently served a paid surface on the way past.
        import builtins

        real_import = builtins.__import__

        def broken(name, *args, **kwargs):
            if name == "config.features":
                raise ImportError("boom")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", broken)
        assert client.get("/audit-log").status_code == 503


class TestListing:
    def test_returns_records_newest_first(self, client):
        audit_log.record("license.activate", target="pro")
        audit_log.record("artifact.delete", target="abc")

        body = client.get("/audit-log").json()
        assert body["total"] == 2
        assert [r["action"] for r in body["records"]] == ["artifact.delete", "license.activate"]

    def test_filters_by_action_prefix(self, client):
        audit_log.record("license.activate")
        audit_log.record("artifact.delete")

        body = client.get("/audit-log?action_prefix=license.").json()
        assert [r["action"] for r in body["records"]] == ["license.activate"]

    def test_filters_by_outcome(self, client):
        audit_log.record("license.activate", outcome="success")
        audit_log.record("license.activate", outcome="denied")

        body = client.get("/audit-log?outcome=denied").json()
        assert len(body["records"]) == 1

    def test_rejects_an_out_of_range_limit(self, client):
        assert client.get("/audit-log?limit=0").status_code == 422
        assert client.get("/audit-log?limit=5000").status_code == 422

    def test_an_empty_log_is_an_empty_list(self, client):
        body = client.get("/audit-log").json()
        assert body["records"] == []
        assert body["total"] == 0


class TestVerifyEndpoint:
    def test_an_untouched_log_verifies(self, client):
        for i in range(3):
            audit_log.record(f"a.{i}")

        body = client.get("/audit-log/verify").json()
        assert body["ok"] is True
        assert body["checked"] == 3
        assert body["broken_at"] is None

    def test_a_tampered_log_reports_200_with_ok_false(self, client):
        # 200, not 5xx. A 500 would make "the check could not run" and "the
        # check failed" the same status — the substitution this subsystem
        # exists to avoid.
        audit_log.record("license.activate", actor="operator")
        audit_log.record("artifact.delete")

        path = audit_log.segments()[0]
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        records[0]["actor"] = "someone-else"
        path.write_text(
            "".join(
                json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records
            )
        )

        resp = client.get("/audit-log/verify")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["broken_at"] == 0
        assert body["reason"]
