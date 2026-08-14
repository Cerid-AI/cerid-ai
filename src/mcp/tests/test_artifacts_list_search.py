# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""GET /artifacts ``search`` filter contract (2026-07-10).

Callers were already sending ``?search=`` and FastAPI silently dropped the
unknown param — a filtered-delete loop walked UNFILTERED pages as a result
(the artifact-purge incident). These tests pin that the param is real:
it reaches the Cypher as a filename/summary substring condition, and its
absence leaves the query unfiltered.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.db.neo4j.artifacts import count_artifacts, list_artifacts


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


# ---------------------------------------------------------------------------
# count_artifacts — AF-093 / WB-24: same filters as list_artifacts, count only
# ---------------------------------------------------------------------------


def _run_count_capture(**kwargs):
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value.single.return_value = {"total": 0}
    count_artifacts(driver, **kwargs)
    call = session.run.call_args
    return call.args[0], call.kwargs


def test_count_has_no_skip_or_limit():
    query, params = _run_count_capture(domain="coding")
    assert "SKIP" not in query
    assert "LIMIT" not in query
    assert "count(a)" in query
    assert "offset" not in params
    assert "limit" not in params


def test_count_search_reaches_cypher_as_substring_filter():
    query, params = _run_count_capture(search="EVAL_SEED")
    assert "toLower(a.filename) CONTAINS toLower($search)" in query
    assert params["search"] == "EVAL_SEED"


def test_count_returns_the_row_total():
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value.single.return_value = {"total": 15000}
    assert count_artifacts(driver, domain="coding") == 15000


def test_count_returns_zero_when_no_row():
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value.single.return_value = None
    assert count_artifacts(driver) == 0


# ---------------------------------------------------------------------------
# include_machine (UX-26) — the default Library view hides machine names
# ---------------------------------------------------------------------------


def test_default_includes_machine_names_for_internal_callers():
    query, _params = _run_capture()
    assert "memory_" not in query, "DB-layer default must not change behavior"


def test_include_machine_false_excludes_machine_names():
    query, _params = _run_capture(include_machine=False)
    for marker in (
        "'memory_'", "'audit-tr_'", "'e2e-marker-'", "'preservation-probe-'",
        "[0-9a-fA-F]{16,}",
    ):
        assert marker in query, f"machine-name exclusion missing: {marker}"
    # Null filenames are machine noise, not null-propagated out of both views.
    assert "coalesce(a.filename, '')" in query


def test_http_endpoint_defaults_to_hiding_machine_names():
    """The route's default is the Library policy; opt back in via query."""
    import inspect

    from app.routers.artifacts import list_artifacts_endpoint

    param = inspect.signature(list_artifacts_endpoint).parameters["include_machine"]
    assert param.default.default is False


def test_count_include_machine_matches_list_population():
    """UX-28 guard: the total must count the same population the page shows."""
    query, _params = _run_count_capture(include_machine=False)
    assert "'memory_'" in query
    assert "coalesce(a.filename, '')" in query
