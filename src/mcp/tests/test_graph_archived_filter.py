# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Graph reads must exclude soft-deleted artifacts, like the vector arm does.

``hide_content`` (app/services/content_lifecycle.py) soft-deletes by setting
``a.archived = true`` via ``set_archived``; chunks deliberately stay in the
stores. The vector arm has honoured that flag since AF-001 and is backstopped by
the ``vector_visible_archived`` startup invariant.

``app/routers/graph.py`` never did. As of the 2026-07-29 GA audit,
``grep -c archived app/routers/graph.py`` returned **0** across 3,281 lines, so
every timeline endpoint counted archived artifacts — and
``/graph/timeline/track/{canonical_id}`` returned their ``filename`` and
``summary`` outright. It went unobserved only because the live KB happened to
hold zero archived artifacts: the product had a working delete button whose
effect the graph surface ignored.

This is a source-contract test rather than a live-data one precisely because
the defect is invisible against an empty archive.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_GRAPH_PY = Path(__file__).resolve().parents[1] / "app" / "routers" / "graph.py"
_SOURCE = _GRAPH_PY.read_text(encoding="utf-8")

# The predicate every :Artifact read must carry. `coalesce` because artifacts
# written before the flag existed have no `archived` property at all, and
# `a.archived = false` would silently drop all of them.
_PREDICATE = "coalesce(a.archived, false) = false"


def _artifact_query_lines() -> list[tuple[int, str]]:
    """Every line binding an ``a:Artifact`` pattern."""
    return [
        (i, line)
        for i, line in enumerate(_SOURCE.splitlines(), start=1)
        if re.search(r"\(a:Artifact\)", line)
    ]


def test_graph_module_filters_archived_at_all():
    """The zero-occurrence state that let the leak ship must not return."""
    assert _PREDICATE in _SOURCE, (
        "graph.py contains no archived filter whatsoever — soft-deleted content "
        "is being served from the graph surface."
    )


def test_every_artifact_query_filters_archived():
    """Each :Artifact binding needs the predicate within its own query block."""
    lines = _SOURCE.splitlines()
    unguarded: list[str] = []

    for lineno, line in _artifact_query_lines():
        # Look ahead within the same Cypher statement (bounded window) for the
        # predicate; queries here are short and the WHERE always follows.
        window = "\n".join(lines[lineno - 1: lineno + 8])
        if _PREDICATE not in window:
            unguarded.append(f"{_GRAPH_PY.name}:{lineno}: {line.strip()}")

    assert not unguarded, (
        "these graph queries read :Artifact without excluding archived "
        "content:\n  " + "\n  ".join(unguarded)
    )


def test_predicate_tolerates_artifacts_predating_the_flag():
    """`a.archived = false` would drop every pre-flag artifact — reject it."""
    naked = re.findall(r"(?<!coalesce\()a\.archived\s*=\s*false", _SOURCE)
    assert not naked, (
        "found a bare `a.archived = false` comparison; artifacts written before "
        "the archived flag existed have no such property and would vanish from "
        "the graph entirely. Use coalesce(a.archived, false) = false."
    )


@pytest.mark.parametrize("endpoint_marker", [
    "MATCH (a:Artifact)-[m:MENTIONS]->(e:Entity {canonical_id: $canonical_id})",
    "MATCH (a:Artifact)-[:MENTIONS]->(focal:Entity {canonical_id: $canonical_id})",
])
def test_track_endpoint_queries_are_guarded(endpoint_marker):
    """/graph/timeline/track returns filename + summary — the direct leak."""
    idx = _SOURCE.find(endpoint_marker)
    assert idx != -1, f"query moved or changed shape: {endpoint_marker}"
    assert _PREDICATE in _SOURCE[idx: idx + 600], (
        "the timeline-track query still returns archived artifacts' filename "
        "and summary text"
    )
