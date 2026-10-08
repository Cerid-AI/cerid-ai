# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""A forgotten conversation stays forgotten on the live stack (the 2026-10-07
resurrection, fnd-9c63675d, as a regression test)."""
from __future__ import annotations

import uuid


def test_forgotten_conversation_is_hidden_refused_and_feeds_clients(http_client, cleanup_ids):
    cid = f"forget-probe-{uuid.uuid4().hex[:8]}"
    cleanup_ids.append(("conversation", cid))
    convo = {"id": cid, "title": "forget probe", "messages": [], "model": "m", "createdAt": 1, "updatedAt": 1}
    assert http_client.post("/user-state/conversations", json=convo).status_code == 200
    resp = http_client.delete(f"/user-state/conversations/{cid}")
    assert resp.status_code == 200 and resp.json()["state"] == "trashed"
    listed = [c["id"] for c in http_client.get("/user-state/conversations").json()]
    assert cid not in listed
    assert http_client.post("/user-state/conversations", json=convo).status_code == 410
    feed = http_client.get("/user-state/forgotten").json()["items"]
    assert any(i["id"] == cid and i["state"] == "trashed" for i in feed)
    purged = http_client.post(
        "/forget", json={"subjects": [{"kind": "conversation", "id": cid}], "mode": "permanent"},
    ).json()
    assert purged["state"] == "purged"
    assert http_client.post("/user-state/conversations", json=convo).status_code == 410
