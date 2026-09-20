# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""GET /archive/files must not list directories outside the archive root (F011)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.upload import router


@pytest.fixture()
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "archive"
    (root / "finance").mkdir(parents=True)
    (root / "finance" / "report.pdf").write_text("x", encoding="utf-8")
    # A sibling directory the caller must never be able to enumerate.
    secrets_dir = tmp_path / "run_secrets"
    secrets_dir.mkdir()
    (secrets_dir / "neo4j_password").write_text("hunter2", encoding="utf-8")

    import config
    monkeypatch.setattr(config, "ARCHIVE_PATH", str(root), raising=False)
    return root


@pytest.fixture()
def client(archive_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize(
    "domain",
    ["../run_secrets", "..", "finance/../../run_secrets", "/etc", "./../run_secrets"],
)
def test_traversal_domain_is_rejected(client: TestClient, domain: str):
    resp = client.get("/archive/files", params={"domain": domain})
    assert resp.status_code == 422, resp.text
    assert "neo4j_password" not in resp.text


def test_traversal_domain_does_not_leak_sibling_filenames(client: TestClient):
    resp = client.get("/archive/files", params={"domain": "../run_secrets"})
    names = [f["filename"] for f in resp.json().get("files", [])] if resp.status_code == 200 else []
    assert "neo4j_password" not in names


def test_legitimate_domain_still_lists(client: TestClient):
    resp = client.get("/archive/files", params={"domain": "finance"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [f["filename"] for f in body["files"]] == ["report.pdf"]
    assert body["files"][0]["path"] == "finance/report.pdf"


def test_no_domain_lists_everything_under_root(client: TestClient):
    resp = client.get("/archive/files")
    assert resp.status_code == 200, resp.text
    assert [f["filename"] for f in resp.json()["files"]] == ["report.pdf"]


def test_unknown_domain_returns_empty_not_error(client: TestClient):
    resp = client.get("/archive/files", params={"domain": "nope"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["files"] == []
