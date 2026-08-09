# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the Briefs read API (Task 2.1a): GET /briefs + GET /briefs/{id}.

Two layers are covered:
* app.db.neo4j.briefs.get_brief / hydrate_claims — exercised directly
  against a mocked Neo4j driver/session (mock_neo4j fixture), asserting
  the Cypher-adjacent row-parsing logic (None on no match, claim id
  filtering, missing-band passthrough).
* app.routers.briefs — exercised via FastAPI TestClient, asserting HTTP
  status codes, the rendered JSON shape, and the missing-band ->
  "unverified" default that the router (not the db layer) enforces.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _brief_node(
    brief_id="brief-1",
    kind="daily",
    generated_at="2026-07-01T06:00:00+00:00",
    sections=None,
    status="generated",
):
    return {
        "brief_id": brief_id,
        "kind": kind,
        "generated_at": generated_at,
        "prompt_version": f"{kind}-v1",
        "status": status,
        "sections_json": json.dumps(sections or {"CONNECTIONS": "A links to B."}),
    }


# ---------------------------------------------------------------------------
# DB layer — app.db.neo4j.briefs.get_brief / hydrate_claims
# ---------------------------------------------------------------------------


class TestGetBrief:
    def test_returns_none_when_no_match(self, mock_neo4j):
        from app.db.neo4j.briefs import get_brief

        driver, session = mock_neo4j
        session.run.return_value.single.return_value = None

        assert get_brief(driver, "unknown-id") is None

    def test_returns_record_with_hydrated_claim_ids(self, mock_neo4j):
        from app.db.neo4j.briefs import get_brief

        driver, session = mock_neo4j
        node = _brief_node()
        row = {"b": node, "claim_ids": ["claim-1", "claim-2", None]}
        session.run.return_value.single.return_value = row

        record = get_brief(driver, "brief-1")

        assert record is not None
        assert record.brief_id == "brief-1"
        assert record.kind == "daily"
        assert record.sections == {"CONNECTIONS": "A links to B."}
        # None entries from the OPTIONAL MATCH collect() are filtered out.
        assert record.claim_ids == ["claim-1", "claim-2"]
        assert isinstance(record.generated_at, datetime)

    def test_falls_back_on_corrupt_sections_json(self, mock_neo4j):
        from app.db.neo4j.briefs import get_brief

        driver, session = mock_neo4j
        node = _brief_node()
        node["sections_json"] = "{not valid json"
        row = {"b": node, "claim_ids": []}
        session.run.return_value.single.return_value = row

        record = get_brief(driver, "brief-1")

        assert record is not None
        assert record.sections == {}


class TestHydrateClaims:
    def test_missing_band_and_text_pass_through_as_none(self, mock_neo4j):
        """DB layer does not default — that is the router's job."""
        from app.db.neo4j.briefs import hydrate_claims

        driver, session = mock_neo4j
        session.run.return_value = [
            {
                "claim_id": "claim-1",
                "text": "The sky is blue.",
                "band": "verified",
                "source_ids": ["artifact-1", None],
            },
            {
                "claim_id": "claim-2",
                "text": None,
                "band": None,
                "source_ids": [],
            },
        ]

        claims = hydrate_claims(driver, "brief-1")

        assert len(claims) == 2
        assert claims[0]["band"] == "verified"
        assert claims[0]["source_ids"] == ["artifact-1"]  # None filtered
        assert claims[1]["band"] is None
        assert claims[1]["text"] is None

    def test_empty_when_brief_cites_nothing(self, mock_neo4j):
        from app.db.neo4j.briefs import hydrate_claims

        driver, session = mock_neo4j
        session.run.return_value = []

        assert hydrate_claims(driver, "brief-1") == []


class TestHydrateClaimsForBriefs:
    """Batch hydrator — one query for many briefs, not N+1."""

    def test_empty_ids_short_circuits_without_a_query(self, mock_neo4j):
        from app.db.neo4j.briefs import hydrate_claims_for_briefs

        driver, session = mock_neo4j

        assert hydrate_claims_for_briefs(driver, []) == {}
        session.run.assert_not_called()

    def test_assembles_per_brief_and_drops_null_claim_rows(self, mock_neo4j):
        from app.db.neo4j.briefs import hydrate_claims_for_briefs

        driver, session = mock_neo4j
        session.run.return_value = [
            {
                "brief_id": "brief-cited",
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "text": "Verified claim.",
                        "band": "verified",
                        "source_ids": ["artifact-1", None],
                    },
                    {
                        "claim_id": "claim-2",
                        "text": None,
                        "band": None,
                        "source_ids": [],
                    },
                ],
            },
            {
                "brief_id": "brief-uncited",
                # OPTIONAL MATCH null-claim row: every field null.
                "claims": [
                    {"claim_id": None, "text": None, "band": None, "source_ids": []},
                ],
            },
        ]

        result = hydrate_claims_for_briefs(driver, ["brief-cited", "brief-uncited"])

        assert session.run.call_args.kwargs["ids"] == ["brief-cited", "brief-uncited"]
        assert len(result["brief-cited"]) == 2
        assert result["brief-cited"][0]["band"] == "verified"
        assert result["brief-cited"][0]["source_ids"] == ["artifact-1"]  # None filtered
        assert result["brief-cited"][1]["band"] is None
        # The uncited brief's null-claim row is dropped -> [], not [{null}].
        assert result["brief-uncited"] == []


# ---------------------------------------------------------------------------
# Router layer — app.routers.briefs via TestClient
# ---------------------------------------------------------------------------


def _make_record(brief_id, kind, generated_at, sections):
    from app.services.briefs.service import BriefRecord

    return BriefRecord(
        brief_id=brief_id,
        kind=kind,
        generated_at=generated_at,
        prompt_version=f"{kind}-v1",
        sections=sections,
        claim_ids=[],
        status="generated",
    )


@pytest.fixture()
def briefs_client():
    from app.routers.briefs import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestListBriefsEndpoint:
    def test_kind_daily_newest_first(self, briefs_client):
        older = _make_record(
            "brief-old", "daily", datetime(2026, 7, 1, tzinfo=timezone.utc),
            {"CONNECTIONS": "old"},
        )
        newer = _make_record(
            "brief-new", "daily", datetime(2026, 7, 2, tzinfo=timezone.utc),
            {"CONNECTIONS": "new"},
        )
        # list_briefs is documented to already return newest-first; the
        # router must preserve that order rather than re-sort/reverse it.
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.db.neo4j.briefs.list_briefs",
                return_value=[newer, older],
            ) as mock_list,
            patch("app.db.neo4j.briefs.hydrate_claims_for_briefs", return_value={}),
        ):
            resp = briefs_client.get("/briefs", params={"kind": "daily"})

        assert resp.status_code == 200
        assert mock_list.call_args.kwargs["kind"] == "daily"
        body = resp.json()
        assert [b["id"] for b in body] == ["brief-new", "brief-old"]
        assert body[0]["kind"] == "daily"
        assert body[0]["sections"] == [{"title": "CONNECTIONS", "body": "new"}]

    def test_kind_weekly(self, briefs_client):
        record = _make_record(
            "weekly-1", "weekly", datetime(2026, 7, 1, tzinfo=timezone.utc),
            {"EMERGING_THESIS": "thesis text"},
        )
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.db.neo4j.briefs.list_briefs", return_value=[record],
            ) as mock_list,
            patch("app.db.neo4j.briefs.hydrate_claims_for_briefs", return_value={}),
        ):
            resp = briefs_client.get("/briefs", params={"kind": "weekly"})

        assert resp.status_code == 200
        assert mock_list.call_args.kwargs["kind"] == "weekly"
        body = resp.json()
        assert len(body) == 1
        assert body[0]["kind"] == "weekly"

    def test_respects_limit_param(self, briefs_client):
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.db.neo4j.briefs.list_briefs", return_value=[]) as mock_list,
            patch("app.db.neo4j.briefs.hydrate_claims_for_briefs", return_value={}),
        ):
            resp = briefs_client.get(
                "/briefs", params={"kind": "daily", "limit": 5},
            )

        assert resp.status_code == 200
        assert mock_list.call_args.kwargs["limit"] == 5

    def test_kind_inbox_is_422(self, briefs_client):
        resp = briefs_client.get("/briefs", params={"kind": "inbox"})
        assert resp.status_code == 422

    def test_kind_bogus_is_422(self, briefs_client):
        resp = briefs_client.get("/briefs", params={"kind": "bogus"})
        assert resp.status_code == 422

    def test_neo4j_failure_degrades_to_empty_list(self, briefs_client):
        with patch("app.deps.get_neo4j", side_effect=RuntimeError("down")):
            resp = briefs_client.get("/briefs", params={"kind": "daily"})

        assert resp.status_code == 200
        assert resp.json() == []

    def test_batches_claim_hydration_not_n_plus_one(self, briefs_client, mock_neo4j):
        """Real list_briefs + hydrate_claims_for_briefs (db layer, not mocked)
        against a mocked driver — proves the list endpoint issues one query
        for the brief list plus ONE batch hydrate query, never N+1 per-brief
        round-trips, and assembles each brief's claims from the batch dict.
        """
        driver, session = mock_neo4j
        node_new = _brief_node("brief-new", generated_at="2026-07-02T06:00:00+00:00")
        node_old = _brief_node("brief-old", generated_at="2026-07-01T06:00:00+00:00")

        session.run.side_effect = [
            [{"b": node_new}, {"b": node_old}],  # list_briefs query
            [
                {
                    "brief_id": "brief-new",
                    "claims": [
                        {
                            "claim_id": "claim-1",
                            "text": "Cited claim.",
                            "band": "verified",
                            "source_ids": ["artifact-1"],
                        },
                    ],
                },
                {
                    "brief_id": "brief-old",
                    # brief-old cites nothing -> null-claim row, must be [].
                    "claims": [
                        {"claim_id": None, "text": None, "band": None, "source_ids": []},
                    ],
                },
            ],  # hydrate_claims_for_briefs batch query
        ]

        with patch("app.deps.get_neo4j", return_value=driver):
            resp = briefs_client.get("/briefs", params={"kind": "daily"})

        assert resp.status_code == 200
        # Exactly 2 round-trips for a 2-brief list: the list query + ONE
        # batch hydrate — not 1 + N per-brief hydrations.
        assert session.run.call_count == 2
        body = resp.json()
        assert [b["id"] for b in body] == ["brief-new", "brief-old"]
        assert len(body[0]["claims"]) == 1
        assert body[0]["claims"][0]["band"] == "verified"
        assert body[1]["claims"] == []


class TestGetBriefEndpoint:
    def test_unknown_id_is_404(self, briefs_client):
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.db.neo4j.briefs.get_brief", return_value=None),
        ):
            resp = briefs_client.get("/briefs/does-not-exist")

        assert resp.status_code == 404

    def test_hydrates_claims_with_missing_band_default(self, briefs_client):
        record = _make_record(
            "brief-1", "daily", datetime(2026, 7, 1, tzinfo=timezone.utc),
            {"CONNECTIONS": "A links to B.", "PATTERN": "x", "QUESTION": "y?"},
        )
        claims = [
            {
                "claim_id": "claim-1",
                "text": "Verified claim text.",
                "band": "verified",
                "source_ids": ["artifact-1"],
            },
            {
                "claim_id": "claim-2",
                "text": None,
                "band": None,  # no verification pass has run yet
                "source_ids": [],
            },
        ]
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.db.neo4j.briefs.get_brief", return_value=record),
            patch("app.db.neo4j.briefs.hydrate_claims", return_value=claims),
        ):
            resp = briefs_client.get("/briefs/brief-1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "brief-1"
        assert body["kind"] == "daily"
        assert len(body["claims"]) == 2
        assert body["claims"][0]["band"] == "verified"
        assert body["claims"][0]["text"] == "Verified claim text."
        assert body["claims"][0]["source_ids"] == ["artifact-1"]
        # Missing band must default to "unverified", never fabricate "verified".
        assert body["claims"][1]["band"] == "unverified"
        # Missing text falls back to the claim_id rather than crashing.
        assert body["claims"][1]["text"] == "claim-2"

    def test_hydration_failure_is_500_not_masked_as_empty_claims(self, briefs_client):
        """A hydration error must fail loud (500) — never render as a
        successful 200 with claims:[], which is reserved for a brief that
        genuinely cites nothing."""
        record = _make_record(
            "brief-1", "daily", datetime(2026, 7, 1, tzinfo=timezone.utc),
            {"CONNECTIONS": "A links to B."},
        )
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.db.neo4j.briefs.get_brief", return_value=record),
            patch(
                "app.db.neo4j.briefs.hydrate_claims",
                side_effect=RuntimeError("claims query down"),
            ),
        ):
            resp = briefs_client.get("/briefs/brief-1")

        assert resp.status_code == 500
        assert "RuntimeError" not in resp.text
        assert "claims query down" not in resp.text

    def test_genuinely_zero_citation_brief_is_200_with_empty_claims(self, briefs_client):
        """When hydrate_claims succeeds but returns no rows, that IS a real
        zero-citation brief -> 200 with claims:[], distinct from a failure."""
        record = _make_record(
            "brief-2", "daily", datetime(2026, 7, 1, tzinfo=timezone.utc),
            {"CONNECTIONS": "Nothing cited yet."},
        )
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.db.neo4j.briefs.get_brief", return_value=record),
            patch("app.db.neo4j.briefs.hydrate_claims", return_value=[]),
        ):
            resp = briefs_client.get("/briefs/brief-2")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "brief-2"
        assert body["claims"] == []

    def test_unexpected_band_value_logs_warning_and_defaults(self, briefs_client, caplog):
        """A leaked/garbage band value (e.g. "uncertain") must still render
        as the safe "unverified" default, but — unlike a missing band — the
        drift must be observable via a warning log."""
        record = _make_record(
            "brief-3", "daily", datetime(2026, 7, 1, tzinfo=timezone.utc),
            {"CONNECTIONS": "x"},
        )
        claims = [
            {
                "claim_id": "claim-drift",
                "text": "Drifted claim.",
                "band": "uncertain",
                "source_ids": [],
            },
        ]
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.db.neo4j.briefs.get_brief", return_value=record),
            patch("app.db.neo4j.briefs.hydrate_claims", return_value=claims),
        ):
            resp = briefs_client.get("/briefs/brief-3")

        assert resp.status_code == 200
        assert resp.json()["claims"][0]["band"] == "unverified"
        assert any(
            "unexpected claim band value" in r.message and "uncertain" in r.message
            for r in caplog.records
        )

    def test_settings_literal_id_is_404_via_guard(self, briefs_client):
        """Defensive guard: a brief_id of 'settings' never returns brief data,
        even if it somehow reached this handler (registration order normally
        routes /briefs/settings to app.routers.brief_settings first)."""
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.db.neo4j.briefs.get_brief") as mock_get_brief,
        ):
            resp = briefs_client.get("/briefs/settings")

        assert resp.status_code == 404
        mock_get_brief.assert_not_called()

    def test_neo4j_failure_is_500_not_stack_leak(self, briefs_client):
        with patch("app.deps.get_neo4j", side_effect=RuntimeError("down")):
            resp = briefs_client.get("/briefs/brief-1")

        assert resp.status_code == 500
        assert "RuntimeError" not in resp.text


# ---------------------------------------------------------------------------
# Route registration order — /briefs/settings must resolve to the static
# brief_settings route, not this router's dynamic /briefs/{brief_id}.
# ---------------------------------------------------------------------------


class TestRouteRegistrationOrder:
    def test_briefs_settings_resolves_to_brief_settings_router(self):
        from app.routers import brief_settings, briefs

        app = FastAPI()
        # Mirror main.py registration order: brief_settings before briefs.
        app.include_router(brief_settings.router)
        app.include_router(briefs.router)
        client = TestClient(app)

        fake_redis = MagicMock()
        fake_redis.get.return_value = None
        with (
            patch("app.deps.get_redis", return_value=fake_redis),
            patch("app.db.neo4j.briefs.get_brief") as mock_get_brief,
        ):
            resp = client.get("/briefs/settings")

        assert resp.status_code == 200
        assert "write_to_vault" in resp.json()
        mock_get_brief.assert_not_called()
