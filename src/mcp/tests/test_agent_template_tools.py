"""Built-in custom-agent templates may only name tools that exist.

Regression guard for the 2026-07-29 agent audit: every built-in template
advertised tools that were absent from the registry — `pkb_verify`,
`web_search`, `pkb_list_artifacts`, `pkb_delete_artifact`. `GET
/custom-agents/templates` served them, the UI badge-rendered them, and a user
creating "Fact Checker" believed it verified claims against external sources.

Nothing validated the names against `get_all_tools()`, so the drift was silent.
"""

import pytest

from app.agents.templates import AGENT_TEMPLATES as TEMPLATES
from app.tools import get_all_tools


@pytest.fixture(scope="module")
def registered_tool_names() -> set[str]:
    return {t["name"] for t in get_all_tools()}


def test_templates_exist():
    assert TEMPLATES, "expected built-in templates"


def test_every_template_tool_is_registered(registered_tool_names):
    """A template naming a nonexistent tool is a promise the product can't keep."""
    unknown: dict[str, list[str]] = {}
    for tpl in TEMPLATES:
        missing = [
            name for name in (tpl.get("tools") or [])
            if name not in registered_tool_names
        ]
        if missing:
            unknown[str(tpl.get("template_id"))] = missing

    assert not unknown, (
        "templates reference tools absent from get_all_tools(): "
        f"{unknown}. Use the registered name (e.g. pkb_artifacts, "
        "pkb_web_search, pkb_check_hallucinations, pkb_artifact_delete)."
    )


def test_templates_have_required_fields():
    required = {"template_id", "name", "description", "system_prompt", "tools"}
    for tpl in TEMPLATES:
        missing = required - set(tpl)
        assert not missing, f"{tpl.get('template_id')} missing {missing}"


def test_template_ids_are_unique():
    ids = [t["template_id"] for t in TEMPLATES]
    assert len(ids) == len(set(ids)), f"duplicate template ids: {ids}"
