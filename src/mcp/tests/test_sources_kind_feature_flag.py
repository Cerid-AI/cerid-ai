# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Gates on the source-kind → feature-flag map exposed by GET /sources/kinds.

``KIND_TIER`` only knows core-vs-pro, so a client that wants to explain *which*
paid capability a kind needs had nothing to read. ``KIND_FEATURE_FLAG`` supplies
the pairing; these tests keep it from drifting away from the three things it has
to agree with — ``PRO_KINDS``, ``FEATURE_FLAGS``, and the connector metas.
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.sources import KIND_FEATURE_FLAG, HealthProbeResult
from config.features import FEATURE_FLAGS
from core.ingest.sources.kinds import CORE_KINDS, PRO_KINDS

# --- population: a new Pro kind cannot ship without declaring a flag ---------


def test_every_pro_kind_declares_a_feature_flag():
    missing = [k for k in PRO_KINDS if k not in KIND_FEATURE_FLAG]
    assert not missing, (
        f"Pro kinds with no feature flag: {missing}. Add an entry to "
        "app.routers.sources.KIND_FEATURE_FLAG naming the FEATURE_FLAGS key "
        "that gates the kind."
    )


def test_core_kinds_declare_no_feature_flag():
    # A flag on an ungated kind would read as a gate that does not exist.
    declared = [k for k in CORE_KINDS if k in KIND_FEATURE_FLAG]
    assert not declared, f"Core kinds must not name a feature flag: {declared}"


def test_map_covers_no_unknown_kinds():
    assert set(KIND_FEATURE_FLAG) == set(PRO_KINDS)


# --- the flag names have to be real -----------------------------------------


def test_every_declared_flag_exists_in_feature_flags():
    # is_feature_enabled() fails closed on an unknown key, so a typo here would
    # silently gate the kind off at every tier instead of raising.
    unknown = {
        kind: flag
        for kind, flag in KIND_FEATURE_FLAG.items()
        if flag not in FEATURE_FLAGS
    }
    assert not unknown, f"Kinds naming a non-existent feature flag: {unknown}"


def test_map_agrees_with_connector_metas():
    from app.routers.connectors import _CONNECTORS

    overlap = {slug for slug in _CONNECTORS if slug in KIND_FEATURE_FLAG}
    assert overlap, "guards the fixture — connector slugs should overlap Pro kinds"
    mismatched = {
        slug: (KIND_FEATURE_FLAG[slug], _CONNECTORS[slug].feature_flag)
        for slug in overlap
        if KIND_FEATURE_FLAG[slug] != _CONNECTORS[slug].feature_flag
    }
    assert not mismatched, (
        f"KIND_FEATURE_FLAG disagrees with ConnectorMeta.feature_flag: {mismatched}"
    )


# --- the payload actually carries it ----------------------------------------


def _kinds_payload() -> dict[str, dict]:
    from app.routers.sources import router

    app = FastAPI()
    app.include_router(router)
    # Pin the clipboard heartbeat probe so this stays hermetic (no Redis).
    with patch(
        "app.routers.sources._check_clipboard_daemon",
        return_value=HealthProbeResult(ok=True, detail="heartbeat 1s ago"),
    ):
        resp = TestClient(app).get("/sources/kinds")
    assert resp.status_code == 200
    return {row["kind"]: row for row in resp.json()}


def test_kinds_endpoint_exposes_feature_flag_for_pro_kinds():
    rows = _kinds_payload()
    assert rows["gmail"]["feature_flag"] == "gmail_connector"
    assert rows["apple_mail"]["feature_flag"] == "apple_mail_reader"
    assert rows["meeting_audio"]["feature_flag"] == "meeting_diarization"


def test_kinds_endpoint_reports_null_feature_flag_for_core_kinds():
    rows = _kinds_payload()
    assert rows["folder"]["feature_flag"] is None
    assert rows["rss"]["feature_flag"] is None


def test_kinds_endpoint_matches_the_map_for_every_kind():
    rows = _kinds_payload()
    for kind, row in rows.items():
        assert row["feature_flag"] == KIND_FEATURE_FLAG.get(kind), kind
