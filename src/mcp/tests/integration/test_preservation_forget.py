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


def test_derived_memories_are_previewed_trashed_restored_and_purged(http_client, neo4j_driver, cleanup_ids):
    """Phase 2: what a conversation produced goes with it when the user says so."""
    cid = f"forget-probe-{uuid.uuid4().hex[:8]}"
    aid = f"forgetprobe{uuid.uuid4().hex}"
    mid = f"forgetprobe-{uuid.uuid4().hex}"
    cleanup_ids.append(("conversation", cid))
    with neo4j_driver.session() as s:
        s.run(
            "MERGE (c:Conversation {id: $cid}) "
            "CREATE (a:Artifact {id: $aid, filename: 'memory_fact_probe', summary: 'probe memory'})"
            "-[:EXTRACTED_FROM]->(c) "
            "CREATE (r:VerificationReport {conversation_id: $cid}) "
            "CREATE (m:Memory {id: $mid, text: 'probe verified fact', status: 'active'})-[:VERIFIED_BY]->(r)",
            cid=cid, aid=aid, mid=mid,
        )
    try:
        preview = http_client.post("/forget/preview", json={"kind": "conversation", "id": cid}).json()
        groups = {g["key"]: g for g in preview["groups"]}
        assert [i["id"] for i in groups["memories"]["items"]] == [aid]
        assert [i["id"] for i in groups["verified_memories"]["items"]] == [mid]

        subjects = [
            {"kind": "conversation", "id": cid},
            {"kind": "artifact", "id": aid},
            {"kind": "memory", "id": mid},
        ]
        trashed = http_client.post("/forget", json={"subjects": subjects}).json()
        with neo4j_driver.session() as s:
            row = s.run(
                "MATCH (a:Artifact {id: $aid}), (m:Memory {id: $mid}) RETURN a.archived AS archived, m.status AS status",
                aid=aid, mid=mid,
            ).single()
        assert row["archived"] is True and row["status"] == "forgotten"
        assert any(g["forget_id"] == trashed["forget_id"] for g in http_client.get("/forget/trash").json()["items"])

        assert http_client.post(f"/forget/{trashed['forget_id']}/restore").json()["restored"] == 3
        with neo4j_driver.session() as s:
            row = s.run(
                "MATCH (a:Artifact {id: $aid}), (m:Memory {id: $mid}) RETURN a.archived AS archived, m.status AS status",
                aid=aid, mid=mid,
            ).single()
        assert not row["archived"] and row["status"] == "active"

        gone = http_client.post("/forget", json={"subjects": subjects, "mode": "permanent"}).json()
        assert gone["state"] == "purged"
        receipt = http_client.get(f"/forget/receipts/{gone['forget_id']}").json()
        assert receipt["adapters"]["artifacts"]["removed"] >= 1
        assert receipt["adapters"]["verified_memories"]["removed"] == 1
        with neo4j_driver.session() as s:
            left = s.run(
                "OPTIONAL MATCH (a:Artifact {id: $aid}) OPTIONAL MATCH (m:Memory {id: $mid}) "
                "OPTIONAL MATCH (c:Conversation {id: $cid}) RETURN a, m, c",
                aid=aid, mid=mid, cid=cid,
            ).single()
        assert left["a"] is None and left["m"] is None and left["c"] is None
    finally:
        with neo4j_driver.session() as s:
            s.run(
                "MATCH (n) WHERE n.id IN [$aid, $mid, $cid] OR n.conversation_id = $cid DETACH DELETE n",
                aid=aid, mid=mid, cid=cid,
            )
