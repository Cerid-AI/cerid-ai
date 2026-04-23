# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /custom-agents/from-template/{id} contract test.

Backstops two layered failures from the 2026-04-23 incident:

1. Frontend ``AgentTemplate.id`` mismatched backend ``template_id`` so the
   URL became ``/from-template/undefined`` — caught by
   test_frontend_backend_route_contract via the templates GET response shape.

2. Backend ``AgentCreateRequest`` required ``body.name`` even when overrides
   was empty, so the empty ``{}`` body the frontend sends 422'd. The fix
   introduced ``AgentTemplateOverrides`` (every field optional). This test
   asserts:
   - empty body succeeds and returns the template defaults
   - overriding ``name`` actually replaces the template default
   - the response carries the ``template_id`` (which the frontend reads
     to display the "tmpl: <id>" badge)
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app():
    """Mount the custom-agents router only — keeps the test focused."""
    from app.routers.custom_agents import router

    app = FastAPI()
    app.include_router(router)
    return app


def _stub_create_agent(driver, **params):
    """Echo back the params plus a fake id — what create_agent would do."""
    return {
        "agent_id": "stub-id",
        "created_at": "2026-04-23T00:00:00+00:00",
        "updated_at": "2026-04-23T00:00:00+00:00",
        **params,
    }


@patch("app.db.neo4j.agents.create_agent", side_effect=_stub_create_agent)
@patch("app.routers.custom_agents.get_neo4j", return_value=object())
def test_from_template_with_empty_body_succeeds(_mock_neo4j, _mock_create):
    """Empty ``{}`` body must not 422 — overrides are all-optional.

    The 2026-04-23 bug: AgentCreateRequest required ``name``, so the
    frontend's empty-body POST got 422 with "Field required". The
    AgentTemplateOverrides model fixes this.
    """
    client = TestClient(_make_app())
    res = client.post("/custom-agents/from-template/research-assistant", json={})

    assert res.status_code == 201, f"expected 201, got {res.status_code}: {res.text}"
    body = res.json()
    # Template defaults must flow through
    assert body["name"] == "Research Assistant"
    assert body["template_id"] == "research-assistant"
    # System prompt and tools should be the template's
    assert body["system_prompt"]
    assert "pkb_query" in body["tools"]


@patch("app.db.neo4j.agents.create_agent", side_effect=_stub_create_agent)
@patch("app.routers.custom_agents.get_neo4j", return_value=object())
def test_from_template_overrides_replace_defaults(_mock_neo4j, _mock_create):
    """Sending overrides must actually replace the matching template fields."""
    client = TestClient(_make_app())
    res = client.post(
        "/custom-agents/from-template/research-assistant",
        json={"name": "My Research Bot", "temperature": 0.1},
    )

    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "My Research Bot"
    assert body["temperature"] == 0.1
    # Non-overridden fields stay at template defaults
    assert body["template_id"] == "research-assistant"


@patch("app.routers.custom_agents.get_neo4j", return_value=object())
def test_from_template_unknown_id_returns_404(_mock_neo4j):
    """Unknown template id should be 404, not 500 or "Internal Server Error"."""
    client = TestClient(_make_app())
    res = client.post("/custom-agents/from-template/no-such-template", json={})
    assert res.status_code == 404
    assert "no-such-template" in res.json()["detail"]


def test_templates_endpoint_returns_template_id_field():
    """The frontend reads ``template_id`` (NOT ``id``) from this response.

    The 2026-04-23 bug: frontend ``AgentTemplate.id`` didn't match the
    backend's ``template_id`` field, so URL params were ``undefined``.
    This is the regression guard at the response-shape level.
    """
    client = TestClient(_make_app())
    res = client.get("/custom-agents/templates")
    assert res.status_code == 200
    body = res.json()
    assert "templates" in body
    assert body["templates"], "expected at least one built-in template"
    for tpl in body["templates"]:
        assert "template_id" in tpl, (
            f"template missing 'template_id' (frontend reads this field): {tpl}"
        )
        assert tpl["template_id"], "template_id must not be empty"
