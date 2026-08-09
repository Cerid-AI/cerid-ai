"""The triage LangGraph must tolerate a fan-in write to its state channels.

Regression guard for the 2026-07-29 agent audit: `/agent/triage`,
`/agent/triage/batch` and the workflow `triage` node returned 500 on every call
whenever Sentry was enabled (the shipped default).

Cause: the graph was built as ``StateGraph(dict)``, putting all state in one
``__root__`` LastValue channel that accepts a single writer per superstep.
``extract_metadata`` has two inbound edges, and Sentry's LangGraph integration
calls ``get_graph()`` on compile to draw the topology — that draw simulates
writes along both edges at once and raised ``InvalidUpdateError``.

``get_graph()`` is therefore the regression probe: it is the operation Sentry
performs, and it fails on the old single-channel schema.
"""

import pytest

from app.agents.triage import TriageState, build_triage_graph


def test_graph_compiles():
    assert build_triage_graph().compile() is not None


def test_get_graph_does_not_raise_on_fan_in():
    """The exact call Sentry's LangGraph integration makes at compile time."""
    compiled = build_triage_graph().compile()
    drawn = compiled.get_graph()  # raised InvalidUpdateError pre-fix
    assert drawn.nodes, "drawn graph should expose nodes"
    assert drawn.edges, "drawn graph should expose edges"


def test_state_fields_are_annotated_channels():
    """Every field needs a reducer, or a concurrent write is fatal again.

    A plain (unannotated) field is a single-writer channel — reintroducing one
    on a fan-in path reintroduces the 500.
    """
    hints = TriageState.__annotations__
    assert hints, "TriageState must declare fields"
    unannotated = [
        name for name, hint in hints.items()
        if "Annotated" not in str(hint)
    ]
    assert not unannotated, (
        f"fields without a reducer will break fan-in writes: {unannotated}"
    )


@pytest.mark.asyncio
async def test_triage_file_runs_to_completion(tmp_path):
    """End-to-end: a real file parses without hitting the channel conflict."""
    from app.agents.triage import triage_file

    probe = tmp_path / "note.md"
    probe.write_text(
        "Planning notes.\n\nWe decided to defer the graph refactor to Q3.\n",
        encoding="utf-8",
    )

    result = await triage_file(str(probe), domain="general", categorize_mode="manual")

    assert result.get("status") != "error", result.get("error")
    assert result.get("filename") == "note.md"
    assert result.get("parsed_text")
    assert result.get("metadata")
