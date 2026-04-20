# Copyright (c) 2026 Cerid AI. Apache-2.0 license.
"""Tests for custom agent models and templates (Phase 3 — extensibility)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.templates import AGENT_TEMPLATES
from models.agents import AgentCreateRequest, AgentDefinition

# ---------------------------------------------------------------------------
# AgentDefinition model validation
# ---------------------------------------------------------------------------


class TestAgentDefinition:

    def test_minimal_valid(self):
        agent = AgentDefinition(agent_id="abc-123", name="Test Agent")
        assert agent.agent_id == "abc-123"
        assert agent.name == "Test Agent"
        assert agent.rag_mode == "smart"
        assert agent.temperature == 0.7
        assert agent.tools == []
        assert agent.domains == []

    def test_full_fields(self):
        agent = AgentDefinition(
            agent_id="def-456",
            name="Full Agent",
            description="A fully configured agent",
            system_prompt="You are helpful.",
            tools=["pkb_agent_query", "pkb_web_search"],
            domains=["coding", "finance"],
            rag_mode="simple",
            model_override="openai/gpt-4o",
            temperature=0.3,
            metadata={"source": "test"},
            template_id="research-assistant",
        )
        assert agent.model_override == "openai/gpt-4o"
        assert len(agent.tools) == 2
        assert agent.metadata["source"] == "test"

    def test_name_required(self):
        with pytest.raises(ValidationError):
            AgentDefinition(agent_id="x")

    def test_agent_id_required(self):
        with pytest.raises(ValidationError):
            AgentDefinition(name="No ID")

    def test_temperature_bounds(self):
        with pytest.raises(ValidationError):
            AgentDefinition(agent_id="x", name="Hot", temperature=3.0)
        with pytest.raises(ValidationError):
            AgentDefinition(agent_id="x", name="Cold", temperature=-1.0)

    def test_name_max_length(self):
        with pytest.raises(ValidationError):
            AgentDefinition(agent_id="x", name="A" * 200)

    def test_extra_fields_allowed(self):
        agent = AgentDefinition(agent_id="x", name="Extra", custom_field="hello")
        assert agent.custom_field == "hello"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# AgentCreateRequest validation
# ---------------------------------------------------------------------------


class TestAgentCreateRequest:

    def test_minimal_create(self):
        req = AgentCreateRequest(name="My Agent")
        assert req.name == "My Agent"
        assert req.rag_mode == "smart"
        assert req.temperature == 0.7

    def test_name_is_required(self):
        with pytest.raises(ValidationError):
            AgentCreateRequest()  # type: ignore[call-arg]

    def test_all_optional_defaults(self):
        req = AgentCreateRequest(name="Defaults")
        assert req.description == ""
        assert req.system_prompt == ""
        assert req.tools == []
        assert req.domains == []
        assert req.model_override is None
        assert req.metadata == {}


# ---------------------------------------------------------------------------
# AGENT_TEMPLATES
# ---------------------------------------------------------------------------


class TestAgentTemplates:

    def test_has_four_templates(self):
        assert len(AGENT_TEMPLATES) == 4

    def test_all_have_required_keys(self):
        required = {"template_id", "name", "description", "system_prompt", "rag_mode"}
        for tpl in AGENT_TEMPLATES:
            missing = required - set(tpl.keys())
            assert not missing, f"Template '{tpl.get('name', '?')}' missing keys: {missing}"

    def test_template_ids_unique(self):
        ids = [t["template_id"] for t in AGENT_TEMPLATES]
        assert len(ids) == len(set(ids)), "Duplicate template_id found"

    def test_known_template_ids(self):
        ids = {t["template_id"] for t in AGENT_TEMPLATES}
        expected = {"research-assistant", "code-reviewer", "fact-checker", "knowledge-curator"}
        assert ids == expected

    def test_templates_have_tools(self):
        for tpl in AGENT_TEMPLATES:
            assert "tools" in tpl, f"Template '{tpl['name']}' missing tools list"
            assert isinstance(tpl["tools"], list)
