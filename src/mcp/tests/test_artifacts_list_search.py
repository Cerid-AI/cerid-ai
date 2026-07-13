# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""GET /artifacts ``search`` filter contract (2026-07-10).

Callers were already sending ``?search=`` and FastAPI silently dropped the
unknown param — a filtered-delete loop walked UNFILTERED pages as a result
(the artifact-purge incident). These tests pin that the param is real:
it reaches the Cypher as a filename/summary substring condition, and its
absence leaves the query unfiltered.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.db.neo4j.artifacts import list_artifacts


def _run_capture(**kwargs):
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value = []
    list_artifacts(driver, **kwargs)
    call = session.run.call_args
    return call.args[0], call.kwargs


def test_search_reaches_cypher_as_substring_filter():
    query, params = _run_capture(search="EVAL_SEED")
    assert "toLower(a.filename) CONTAINS toLower($search)" in query
    assert "coalesce(a.summary, '')" in query
    assert params["search"] == "EVAL_SEED"


def test_no_search_leaves_query_unfiltered():
    query, params = _run_capture()
    assert "$search" not in query
    assert "search" not in params


def test_search_composes_with_domain_filter():
    query, params = _run_capture(domain="coding", search="docker")
    assert "d.name = $domain" in query
    assert "$search" in query
    assert " AND " in query
