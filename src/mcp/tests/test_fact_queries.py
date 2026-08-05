# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Phase F (F1) — the bi-temporal :Fact readers (app/db/neo4j/fact_queries.py).

An in-memory fake re-derives each result from the actual Cypher the reader emits
(it reads which WHERE clauses the query string contains and applies the matching
m0006 predicate over in-memory facts), so the tests validate BOTH that the reader
assembles the right clauses and that the m0006 query semantics hold:

  * current  — invalid_at IS NULL AND valid_to IS NULL
  * as-of T  — valid_from <= T AND (valid_to IS NULL OR valid_to > T)  [valid-time only]
  * count(DISTINCT f) — EVENT facts: N dated occurrences = N nodes; STATE: interval admission
  * verification-source exclusion (R5); empty graph returns [] / 0 / set() cleanly.
"""
from __future__ import annotations

from app.db.neo4j.fact_queries import (
    count_facts,
    current_facts,
    facts_as_of,
    subjects_with_current_facts,
)

# ---------------------------------------------------------------------------
# In-memory :Fact graph that interprets the reader Cypher clause-by-clause.
# ---------------------------------------------------------------------------


class _Fact:
    def __init__(
        self,
        uid: str,
        subject_id: str,
        predicate: str,
        valid_from: str,
        *,
        event_date: str = "",
        valid_to: str | None = None,
        invalid_at: str | None = None,
        source: str = "extraction",
        object_id: str | None = None,
        fact_key: str = "",
        created_at: str = "2026-01-01T00:00:00Z",
    ) -> None:
        self.uid = uid
        self.subject_id = subject_id
        self.predicate = predicate
        self.valid_from = valid_from
        self.event_date = event_date
        self.valid_to = valid_to
        self.invalid_at = invalid_at
        self.source = source
        self.object_id = object_id
        self.fact_key = fact_key or (f"{predicate}|{event_date}" if event_date else predicate)
        self.created_at = created_at

    def as_row(self) -> dict:
        return {
            "uid": self.uid, "subject_id": self.subject_id, "object_id": self.object_id,
            "predicate": self.predicate, "fact_key": self.fact_key,
            "event_date": self.event_date, "valid_from": self.valid_from,
            "valid_to": self.valid_to, "invalid_at": self.invalid_at,
            "created_at": self.created_at, "source": self.source,
        }


class _Result:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, facts: list[_Fact]) -> None:
        self._facts = facts

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def _admits(self, f: _Fact, cypher: str, params: dict) -> bool:
        # Subject scoping.
        if "$subject_ids" in cypher:
            if f.subject_id not in params["subject_ids"]:
                return False
        elif f.subject_id != params["subject_id"]:
            return False
        # Temporal predicate.
        if "$t" in cypher:  # as-of T
            t = params["t"]
            lo = f.valid_from in (None, "") or f.valid_from <= t
            hi = f.valid_to is None or f.valid_to > t
            if not (lo and hi):
                return False
        else:  # current
            if f.invalid_at is not None or f.valid_to is not None:
                return False
        # Predicate scope.
        if "$predicate" in cypher and f.predicate != params.get("predicate"):
            return False
        # Verification-source exclusion (R5).
        if "$verification_source" in cypher and (f.source or "") == params.get(
            "verification_source"
        ):
            return False
        return True

    def run(self, cypher: str, **params):
        admitted = [f for f in self._facts if self._admits(f, cypher, params)]
        if "count(" in cypher:
            return _Result([{"n": len(admitted)}])
        if "RETURN DISTINCT f.subject_id" in cypher:
            seen: list[str] = []
            for f in admitted:
                if f.subject_id not in seen:
                    seen.append(f.subject_id)
            return _Result([{"subject_id": s} for s in seen])
        admitted.sort(key=lambda f: (f.valid_from or "", f.uid))
        return _Result([f.as_row() for f in admitted])


class _FakeDriver:
    def __init__(self, facts: list[_Fact] | None = None) -> None:
        self._facts = facts or []

    def session(self) -> _FakeSession:
        return _FakeSession(self._facts)


# ---------------------------------------------------------------------------
# Temporal-reasoning gate: STATE fact closed at T2
# ---------------------------------------------------------------------------

_T1 = "2026-03-01"   # inside the closed interval
_T2 = "2026-05-01"   # the close (valid_to / invalid_at)
_T3 = "2026-06-01"   # after the close
_SUBJECT = "other:yoga-class"


def _closed_state_graph() -> _FakeDriver:
    # One STATE fact valid [2026-02-01, 2026-05-01), belief revised at 2026-05-01.
    return _FakeDriver([
        _Fact("f-state", _SUBJECT, "empirical", "2026-02-01",
              valid_to=_T2, invalid_at=_T2),
    ])


def test_current_excludes_closed_state_fact() -> None:
    assert current_facts(_closed_state_graph(), _SUBJECT) == []


def test_as_of_t1_includes_closed_state_fact() -> None:
    rows = facts_as_of(_closed_state_graph(), _SUBJECT, _T1)
    assert [r["uid"] for r in rows] == ["f-state"]  # inside [valid_from, valid_to)


def test_as_of_t3_excludes_closed_state_fact() -> None:
    assert facts_as_of(_closed_state_graph(), _SUBJECT, _T3) == []  # after close


def test_as_of_ignores_invalid_at() -> None:
    # As-of is pure valid-time: a fact whose belief was revised (invalid_at set)
    # is STILL returned when T falls inside its valid interval.
    rows = facts_as_of(_closed_state_graph(), _SUBJECT, _T1)
    assert rows and rows[0]["invalid_at"] == _T2  # invalidated, yet admitted


# ---------------------------------------------------------------------------
# EVENT facts: N distinct dates = N distinct nodes
# ---------------------------------------------------------------------------


def _event_graph() -> _FakeDriver:
    return _FakeDriver([
        _Fact("e1", _SUBJECT, "conversational", "2026-03-01", event_date="2026-03-01",
              fact_key="conversational|2026-03-01"),
        _Fact("e2", _SUBJECT, "conversational", "2026-03-08", event_date="2026-03-08",
              fact_key="conversational|2026-03-08"),
        _Fact("e3", _SUBJECT, "conversational", "2026-03-15", event_date="2026-03-15",
              fact_key="conversational|2026-03-15"),
    ])


def test_count_event_facts_distinct_dates() -> None:
    assert count_facts(_event_graph(), _SUBJECT, "conversational") == 3


def test_count_predicate_none_counts_all_current() -> None:
    # predicate=None counts every current fact for the subject regardless of type.
    driver = _FakeDriver([
        _Fact("e1", _SUBJECT, "conversational", "2026-03-01", event_date="2026-03-01"),
        _Fact("e2", _SUBJECT, "temporal", "2026-03-08", event_date="2026-03-08"),
    ])
    assert count_facts(driver, _SUBJECT, None) == 2


def test_count_distinct_flag_variants_agree_on_unique_nodes() -> None:
    driver = _event_graph()
    assert count_facts(driver, _SUBJECT, "conversational", distinct=True) == 3
    assert count_facts(driver, _SUBJECT, "conversational", distinct=False) == 3


def test_count_as_of_state_fact() -> None:
    driver = _closed_state_graph()
    assert count_facts(driver, _SUBJECT, "empirical", as_of=_T1) == 1  # inside
    assert count_facts(driver, _SUBJECT, "empirical", as_of=_T3) == 0  # after close
    assert count_facts(driver, _SUBJECT, "empirical") == 0             # current: closed


def test_current_facts_return_open_event_facts() -> None:
    rows = current_facts(_event_graph(), _SUBJECT)
    assert {r["uid"] for r in rows} == {"e1", "e2", "e3"}


# ---------------------------------------------------------------------------
# Verification-source exclusion (R5)
# ---------------------------------------------------------------------------


def _mixed_source_graph() -> _FakeDriver:
    return _FakeDriver([
        _Fact("ex", _SUBJECT, "conversational", "2026-03-01", event_date="2026-03-01",
              source="extraction"),
        _Fact("ve", _SUBJECT, "conversational", "2026-03-08", event_date="2026-03-08",
              source="verification"),
    ])


def test_include_verification_sourced_default_true() -> None:
    rows = current_facts(_mixed_source_graph(), _SUBJECT)
    assert {r["uid"] for r in rows} == {"ex", "ve"}


def test_exclude_verification_sourced() -> None:
    rows = current_facts(
        _mixed_source_graph(), _SUBJECT, include_verification_sourced=False
    )
    assert {r["uid"] for r in rows} == {"ex"}  # verification-derived fact dropped


def test_exclude_verification_sourced_as_of() -> None:
    rows = facts_as_of(
        _mixed_source_graph(), _SUBJECT, _T3, include_verification_sourced=False
    )
    assert {r["uid"] for r in rows} == {"ex"}


def test_null_source_admitted_under_exclusion() -> None:
    # A NULL/blank source is not verification-derived → still admitted when
    # verification facts are excluded (coalesce guard, not a bare <>).
    driver = _FakeDriver([
        _Fact("n0", _SUBJECT, "conversational", "2026-03-01", event_date="2026-03-01",
              source=""),
    ])
    rows = current_facts(driver, _SUBJECT, include_verification_sourced=False)
    assert {r["uid"] for r in rows} == {"n0"}


# ---------------------------------------------------------------------------
# subjects_with_current_facts probe
# ---------------------------------------------------------------------------


def test_subjects_with_current_facts_filters_to_live_subjects() -> None:
    driver = _FakeDriver([
        _Fact("e1", "other:yoga-class", "conversational", "2026-03-01",
              event_date="2026-03-01"),
        _Fact("c1", "other:gym", "empirical", "2026-02-01", valid_to=_T2, invalid_at=_T2),
    ])
    matched = subjects_with_current_facts(
        driver, ["other:yoga-class", "other:gym", "other:nonexistent"]
    )
    assert matched == {"other:yoga-class"}  # gym is closed-only; nonexistent absent


def test_subjects_with_current_facts_empty_input() -> None:
    assert subjects_with_current_facts(_event_graph(), []) == set()


# ---------------------------------------------------------------------------
# Empty graph — clean zero/empty returns (correct on the EMPTY production graph)
# ---------------------------------------------------------------------------


def test_empty_graph_current_facts() -> None:
    assert current_facts(_FakeDriver(), _SUBJECT) == []


def test_empty_graph_facts_as_of() -> None:
    assert facts_as_of(_FakeDriver(), _SUBJECT, _T1) == []


def test_empty_graph_count() -> None:
    assert count_facts(_FakeDriver(), _SUBJECT, None) == 0
    assert count_facts(_FakeDriver(), _SUBJECT, "conversational", as_of=_T1) == 0


def test_empty_graph_subjects_probe() -> None:
    assert subjects_with_current_facts(_FakeDriver(), ["other:x"]) == set()
