# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Memory near-duplicate merge pass (UX-17).

The observed defect: "SOL price down 1.38%" and "SOL is down 1.23%
today" stored as two memories from one conversation. The pass must merge
that pair — and must NOT merge distinct facts that merely share a
subject.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.agents.memory_dedup import (
    find_duplicate_groups,
    memory_similarity,
    normalize_memory_text,
)

# The observed live pair, verbatim shapes from the UX drive report.
SOL_A = {
    "id": "mem-new",
    "text": "SOL price is down 1.23% today",
    "created_at": "2026-08-12T10:00:00+00:00",
}
SOL_B = {
    "id": "mem-old",
    "text": "SOL price down 1.38%",
    "created_at": "2026-08-12T09:00:00+00:00",
}


class TestNormalization:
    def test_numbers_collapse(self):
        assert normalize_memory_text("SOL down 1.38%") == (
            normalize_memory_text("SOL down 1.23%")
        )

    def test_case_and_punctuation_collapse(self):
        assert normalize_memory_text("SOL, price: down!") == "sol price down"


class TestDuplicateGroups:
    def test_the_observed_sol_pair_merges(self):
        groups = find_duplicate_groups([SOL_A, SOL_B])
        assert len(groups) == 1
        group = groups[0]
        assert {m["id"] for m in group} == {"mem-new", "mem-old"}
        assert group[0]["id"] == "mem-new", "the newest memory is the keeper"

    def test_distinct_facts_do_not_merge(self):
        distinct = [
            SOL_A,
            {
                "id": "mem-3",
                "text": "User prefers dark mode in the dashboard",
                "created_at": "2026-08-12T08:00:00+00:00",
            },
            {
                "id": "mem-4",
                "text": "SOL staking rewards accrue daily on the validator",
                "created_at": "2026-08-12T07:00:00+00:00",
            },
        ]
        assert find_duplicate_groups(distinct) == []

    def test_similarity_separates_the_two_cases(self):
        assert memory_similarity(SOL_A["text"], SOL_B["text"]) >= 0.90
        assert memory_similarity(
            SOL_A["text"], "SOL staking rewards accrue daily on the validator",
        ) < 0.90

    def test_a_claim_and_its_negation_never_merge(self):
        """Negation is a meaning flip, not filler: token containment scored
        "User allergic to peanuts" ⊂ "User not allergic to peanuts" as 1.0,
        and the character ratio alone also clears the threshold — merging
        would collapse a fact with its opposite."""
        pairs = [
            ("User allergic to peanuts", "User not allergic to peanuts"),
            ("The rollout can proceed", "The rollout cannot proceed"),
            ("Sarah has never used the staging cluster",
             "Sarah has used the staging cluster"),
        ]
        for a, b in pairs:
            assert memory_similarity(a, b) < 0.90, (a, b)
            assert find_duplicate_groups([
                {"id": "x", "text": a, "created_at": "2026-08-12T10:00:00+00:00"},
                {"id": "y", "text": b, "created_at": "2026-08-12T09:00:00+00:00"},
            ]) == []

    def test_empty_input(self):
        assert find_duplicate_groups([]) == []


@pytest.fixture()
def client():
    from app.main import app

    with patch("app.routers.memories.get_neo4j") as mock_neo4j:
        driver = MagicMock()
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        result = MagicMock()
        result.__iter__ = MagicMock(return_value=iter([SOL_A, SOL_B]))
        session.run.return_value = result
        driver.session.return_value = session
        mock_neo4j.return_value = driver
        yield TestClient(app, raise_server_exceptions=False)


class TestDedupEndpoint:
    def test_dry_run_reports_without_marking(self, client):
        with patch(
            "core.agents.memory_consolidation.mark_superseded"
        ) as mock_mark:
            res = client.post("/memories/dedup", json={})
        assert res.status_code == 200
        data = res.json()
        assert data["dry_run"] is True
        assert data["duplicate_groups"] == 1
        assert data["memories_superseded"] == 0
        mock_mark.assert_not_called()

    def test_confirm_supersedes_older_duplicates(self, client):
        with patch(
            "core.agents.memory_consolidation.mark_superseded"
        ) as mock_mark:
            res = client.post("/memories/dedup", json={"confirm": True})
        assert res.status_code == 200
        data = res.json()
        assert data["memories_superseded"] == 1
        mock_mark.assert_called_once()
        _driver, old_id, new_id = mock_mark.call_args.args
        assert old_id == "mem-old"
        assert new_id == "mem-new"
