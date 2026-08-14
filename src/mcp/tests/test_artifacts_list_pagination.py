# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""WB-24 — GET /artifacts reports a true total instead of the capped page.

Previously the Library pane fetched a single hardcoded ``limit=200`` page and
treated its length as the total, so "Showing 200 of 200" was really "Showing
200 of at-least-200". The endpoint now returns ``X-Total-Count`` /
``X-Has-More`` headers derived from a count-only query, and AF-093's
``count_artifacts`` (used here too) mirrors ``list_artifacts``'s filters.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.artifacts import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_total_count_header_reflects_true_total_beyond_the_page():
    """A domain with 350 artifacts, fetched at limit=200, must report
    X-Total-Count: 350 and X-Has-More: true — not "200 of 200"."""
    page = [{"id": f"a{i}"} for i in range(200)]
    with (
        patch("app.routers.artifacts.get_neo4j", return_value=MagicMock()),
        patch("app.routers.artifacts.graph.list_artifacts", return_value=page),
        patch("app.routers.artifacts.graph.count_artifacts", return_value=350),
    ):
        res = _client().get("/artifacts", params={"limit": 200, "offset": 0})
    assert res.status_code == 200
    assert len(res.json()) == 200
    assert res.headers["X-Total-Count"] == "350"
    assert res.headers["X-Has-More"] == "true"


def test_has_more_false_when_page_reaches_the_end():
    page = [{"id": f"a{i}"} for i in range(50)]
    with (
        patch("app.routers.artifacts.get_neo4j", return_value=MagicMock()),
        patch("app.routers.artifacts.graph.list_artifacts", return_value=page),
        patch("app.routers.artifacts.graph.count_artifacts", return_value=250),
    ):
        res = _client().get("/artifacts", params={"limit": 50, "offset": 200})
    assert res.headers["X-Total-Count"] == "250"
    assert res.headers["X-Has-More"] == "false"


def test_count_artifacts_receives_the_same_filters_as_list_artifacts():
    with (
        patch("app.routers.artifacts.get_neo4j", return_value=MagicMock()),
        patch("app.routers.artifacts.graph.list_artifacts", return_value=[]) as mock_list,
        patch("app.routers.artifacts.graph.count_artifacts", return_value=0) as mock_count,
    ):
        _client().get("/artifacts", params={"domain": "coding", "tag": "python"})
    list_kwargs = mock_list.call_args.kwargs
    count_kwargs = mock_count.call_args.kwargs
    assert list_kwargs["domain"] == count_kwargs["domain"] == "coding"
    assert list_kwargs["tag"] == count_kwargs["tag"] == "python"
    # count_artifacts has no page — offset/limit are list_artifacts-only.
    assert "offset" not in count_kwargs
    assert "limit" not in count_kwargs
