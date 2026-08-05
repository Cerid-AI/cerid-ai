# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""WK2 — default-hide client/internal domains in the wiki list path.

The bare ``MATCH (e:Entity)`` browse path in ``list_top_entities`` must
exclude the client-data domains (``boardroom_foundation``,
``canary_client_domain``) by default, and include them only when
``include_internal=True``.  The exclusion threads:

    list_top_entities (adapter)  ──►  list_entities (service)
        ──►  GET /wiki/entities  +  GET /wiki/index  (routes)

These tests assert the Cypher predicate + params (adapter), the
service threading, and the route query-param wiring — all without a live
Neo4j (the session.run call is captured on a MagicMock).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Adapter-level: list_top_entities excludes hidden domains by default
# ---------------------------------------------------------------------------


def _capture_session(driver: MagicMock) -> dict[str, Any]:
    """Wire a MagicMock driver so session.run captures its cypher + kwargs.

    Returns a dict that the test reads after invoking list_top_entities:
        {"cypher": <str>, "params": <dict>}.
    """
    captured: dict[str, Any] = {}

    def _run(cypher: str, **kwargs: Any) -> list[Any]:
        captured["cypher"] = cypher
        captured["params"] = kwargs
        return []

    session = MagicMock()
    session.run.side_effect = _run
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    driver.session.return_value = session
    return captured


class TestAdapterHiddenDomains:
    def test_default_excludes_hidden_domains(self) -> None:
        from app.db.neo4j import wiki as wiki_adapter

        driver = MagicMock()
        captured = _capture_session(driver)

        wiki_adapter.list_top_entities(driver, limit=30)

        cypher = captured["cypher"]
        params = captured["params"]
        # Predicate present
        assert "NOT e.primary_domain IN $hidden" in cypher
        # Hidden set bound and contains both client-data domains
        assert "hidden" in params
        assert "boardroom_foundation" in params["hidden"]
        assert "canary_client_domain" in params["hidden"]

    def test_include_internal_drops_exclusion(self) -> None:
        from app.db.neo4j import wiki as wiki_adapter

        driver = MagicMock()
        captured = _capture_session(driver)

        wiki_adapter.list_top_entities(driver, limit=30, include_internal=True)

        cypher = captured["cypher"]
        params = captured["params"]
        assert "NOT e.primary_domain IN $hidden" not in cypher
        # No hidden param bound on the include path.
        assert "hidden" not in params

    def test_default_set_is_the_two_client_domains(self) -> None:
        from app.db.neo4j import wiki as wiki_adapter

        assert wiki_adapter.CLIENT_INTERNAL_DOMAINS == {
            "boardroom_foundation",
            "canary_client_domain",
        }

    def test_env_override_replaces_hidden_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.db.neo4j import wiki as wiki_adapter

        monkeypatch.setenv("WIKI_HIDDEN_DOMAINS", "alpha_domain, beta_domain")
        hidden = wiki_adapter._hidden_domains()
        assert hidden == {"alpha_domain", "beta_domain"}

    def test_hidden_domains_default_without_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.db.neo4j import wiki as wiki_adapter

        monkeypatch.delenv("WIKI_HIDDEN_DOMAINS", raising=False)
        assert wiki_adapter._hidden_domains() == wiki_adapter.CLIENT_INTERNAL_DOMAINS


# ---------------------------------------------------------------------------
# Service-level: list_entities threads include_internal to the adapter
# ---------------------------------------------------------------------------


class TestServiceThreading:
    @pytest.mark.asyncio
    async def test_default_passes_include_internal_false(self) -> None:
        from app.services.wiki_pages import list_entities

        driver = MagicMock()
        with patch(
            "app.services.wiki_pages._neo4j_adapter.list_top_entities",
            return_value=[],
        ) as mock_adapter:
            await list_entities(driver, limit=30)

        _, kwargs = mock_adapter.call_args
        assert kwargs.get("include_internal") is False

    @pytest.mark.asyncio
    async def test_include_internal_true_threaded(self) -> None:
        from app.services.wiki_pages import list_entities

        driver = MagicMock()
        with patch(
            "app.services.wiki_pages._neo4j_adapter.list_top_entities",
            return_value=[],
        ) as mock_adapter:
            await list_entities(driver, limit=30, include_internal=True)

        _, kwargs = mock_adapter.call_args
        assert kwargs.get("include_internal") is True


# ---------------------------------------------------------------------------
# Route-level: /wiki/entities + /wiki/index forward include_internal
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    from app.routers.wiki import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestRouteWiring:
    def test_entities_default_include_internal_false(self, client: TestClient) -> None:
        captured: dict[str, Any] = {}

        async def _mock_list(
            driver: Any,
            *,
            limit: int = 30,
            search: str | None = None,
            include_internal: bool = False,
        ) -> list[Any]:
            captured["include_internal"] = include_internal
            return []

        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch("app.routers.wiki.list_entities", new=_mock_list),
        ):
            resp = client.get("/wiki/entities")

        assert resp.status_code == 200
        assert captured["include_internal"] is False

    def test_entities_include_internal_true(self, client: TestClient) -> None:
        captured: dict[str, Any] = {}

        async def _mock_list(
            driver: Any,
            *,
            limit: int = 30,
            search: str | None = None,
            include_internal: bool = False,
        ) -> list[Any]:
            captured["include_internal"] = include_internal
            return []

        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch("app.routers.wiki.list_entities", new=_mock_list),
        ):
            resp = client.get("/wiki/entities?include_internal=true")

        assert resp.status_code == 200
        assert captured["include_internal"] is True

    def test_index_default_include_internal_false(self, client: TestClient) -> None:
        captured: dict[str, Any] = {}

        async def _mock_list(
            driver: Any,
            *,
            limit: int = 30,
            search: str | None = None,
            include_internal: bool = False,
        ) -> list[Any]:
            captured["include_internal"] = include_internal
            return []

        # /index imports list_entities lazily from app.services.wiki_pages,
        # so patch it there.
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.services.wiki_pages.list_entities", new=_mock_list),
        ):
            resp = client.get("/wiki/index")

        assert resp.status_code == 200
        assert captured["include_internal"] is False

    def test_index_include_internal_true(self, client: TestClient) -> None:
        captured: dict[str, Any] = {}

        async def _mock_list(
            driver: Any,
            *,
            limit: int = 30,
            search: str | None = None,
            include_internal: bool = False,
        ) -> list[Any]:
            captured["include_internal"] = include_internal
            return []

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.services.wiki_pages.list_entities", new=_mock_list),
        ):
            resp = client.get("/wiki/index?include_internal=true")

        assert resp.status_code == 200
        assert captured["include_internal"] is True
