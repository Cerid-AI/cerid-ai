# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""TDD tests for Phase 4.2 — reversible, chunked entity merge/unmerge +
embedding-merge candidate generation + LLM adjudication.

Covered:
  - merge_entities re-points EVERY entity-adjacent edge type (MENTIONS,
    CO_MENTIONED in/out, SIMILAR_TO in/out, IN_COMMUNITY, HAS_CONTRADICTION,
    ENRICHED_FROM, HAS_FACT, FACT_OBJECT — the last two written against the
    m0004/m0006 :Fact schema even though production has zero facts).
  - dedup after re-point (weight summed / score max'd / edge collapsed).
  - merge provenance recorded (MergedEntity tombstone + MERGED_INTO edge).
  - unmerge_entity restores identity + MENTIONS and decrements the survivor.
  - reconcile_fact_subjects fixes up the loser's :Fact subject_id/uid onto
    the survivor (no collision), folds a colliding survivor fact (interval
    union + source union) and deletes the duplicate, re-points object_id
    property-only with a binary-fact warning, and drains idempotently.
  - unmerge_entity warns when the survivor still carries :Fact nodes (fact
    reconciliation is one-way — no tombstone snapshot to replay).
  - chunked UNWIND respected (a >chunk-size fixture re-points in batches).
  - candidate generation bands (auto vs adjudicate) + cross-type isolation.
  - adjudication routing: auto merges directly, borderline → LLM, bound honored.
  - embedding-resolution dry-run writes nothing; flag-off is a no-op.

The fake Neo4j driver models the graph as typed edge tuples and interprets the
exact Cypher merge_entities / unmerge_entity issue — so post-state assertions
are semantic, not string-matching.
"""
from __future__ import annotations

import json
from typing import Any, Literal

import pytest

# ---------------------------------------------------------------------------
# In-memory fake Neo4j driver
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def single(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def data(self) -> list[dict[str, Any]]:
        return self._rows


class _Graph:
    """Typed-edge graph state the fake session mutates."""

    def __init__(self) -> None:
        # canonical_id -> props (name, entity_type, mention_count, ...)
        self.entities: dict[str, dict[str, Any]] = {}
        # canonical_id -> tombstone props
        self.tombstones: dict[str, dict[str, Any]] = {}
        # each edge: {"type","src","dst","props"} — src/dst are node-ref strings
        self.edges: list[dict[str, Any]] = []
        # (tombstone_cid) -> survivor_id  for MERGED_INTO
        self.merged_into: dict[str, str] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    # -- seed helpers --------------------------------------------------
    def add_entity(self, cid: str, name: str, etype: str, mentions: int = 1) -> None:
        self.entities[cid] = {
            "canonical_id": cid, "name": name,
            "entity_type": etype, "mention_count": mentions,
        }

    def add_edge(self, etype: str, src: str, dst: str, **props: Any) -> None:
        self.edges.append({"type": etype, "src": src, "dst": dst, "props": dict(props)})

    def edges_of(self, etype: str, *, src: str | None = None, dst: str | None = None) -> list[dict]:
        return [
            e for e in self.edges
            if e["type"] == etype
            and (src is None or e["src"] == src)
            and (dst is None or e["dst"] == dst)
        ]


class _FakeSession:
    def __init__(self, g: _Graph) -> None:
        self._g = g

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *_: Any) -> Literal[False]:
        return False

    # -- edge re-point engine -----------------------------------------
    def _repoint(
        self, etype: str, loser: str, survivor: str, loser_at: str,
        limit: int, merge_kind: str, source_keyed: bool = False,
    ) -> int:
        matching = self._g.edges_of(
            etype, **({"src": loser} if loser_at == "src" else {"dst": loser})
        )
        batch = matching[:limit]
        for edge in batch:
            self._g.edges.remove(edge)
            other = edge["dst"] if loser_at == "src" else edge["src"]
            if merge_kind in ("weight", "score") and other == survivor:
                continue  # self-loop guard
            new_src = survivor if loser_at == "src" else edge["src"]
            new_dst = edge["dst"] if loser_at == "src" else survivor
            src_key = edge.get("props", {}).get("source") if source_keyed else None
            existing = [
                e for e in self._g.edges
                if e["type"] == etype and e["src"] == new_src and e["dst"] == new_dst
                and (not source_keyed or e["props"].get("source") == src_key)
            ]
            if existing:
                cur = existing[0]
                if merge_kind == "weight":
                    cur["props"]["weight"] = cur["props"].get("weight", 0) + edge["props"].get("weight", 0)
                elif merge_kind == "score":
                    cur["props"]["score"] = max(cur["props"].get("score", 0), edge["props"].get("score", 0))
                continue
            self._g.edges.append({
                "type": etype, "src": new_src, "dst": new_dst, "props": dict(edge["props"]),
            })
        return len(batch)

    def run(self, cypher: str, **kw: Any) -> _Result:  # noqa: C901, PLR0911, PLR0912
        self._g.calls.append((cypher, kw))
        g = self._g
        limit = kw.get("limit", 10**9)

        # --- reads --------------------------------------------------
        if "RETURN e.name AS name" in cypher and "coalesce(e.mention_count, 0) AS mention_count" in cypher:
            ent = g.entities.get(kw["canonical_id"])
            if ent is None:
                return _Result([])
            return _Result([{
                "name": ent["name"], "entity_type": ent["entity_type"],
                "mention_count": ent.get("mention_count", 0),
            }])
        if "RETURN a.id AS art_id" in cypher:  # snapshot MENTIONS
            rows = [
                {"art_id": e["src"], "confidence": e["props"].get("confidence"),
                 "chunk_ids": e["props"].get("chunk_ids"), "created_at": e["props"].get("created_at")}
                for e in g.edges_of("MENTIONS", dst=kw["loser_id"])
            ]
            rows.sort(key=lambda r: r["art_id"])
            skip = kw.get("skip", 0)
            return _Result(rows[skip:skip + limit])
        if "RETURN count(*) AS fact_count" in cypher:  # survivor HAS_FACT probe
            n = len(g.edges_of("HAS_FACT", src=kw["survivor_id"]))
            return _Result([{"fact_count": n}])
        if "RETURN type(r) AS rel_type" in cypher:  # leftover guard
            loser = kw["loser_id"]
            counts: dict[str, int] = {}
            for e in g.edges:
                if e["src"] == loser or e["dst"] == loser:
                    counts[e["type"]] = counts.get(e["type"], 0) + 1
            return _Result([{"rel_type": t, "cnt": c} for t, c in counts.items()])
        if "MATCH (t:MergedEntity {canonical_id: $loser_id})-[:MERGED_INTO]" in cypher:
            tomb = g.tombstones.get(kw["loser_id"])
            if tomb is None:
                return _Result([])
            return _Result([{
                "name": tomb["name"], "entity_type": tomb["entity_type"],
                "mention_count": tomb["mention_count"],
                "mentions_snapshot": tomb["mentions_snapshot"],
                "survivor_id": g.merged_into.get(kw["loser_id"]),
            }])

        # --- edge re-points (combined fetch+repoint+count) ----------
        if "MERGE (a)-[m_new:MENTIONS]->(survivor)" in cypher:
            n = self._repoint("MENTIONS", kw["loser_id"], kw["survivor_id"], "dst", limit, "plain")
            return _Result([{"processed": n}])
        if "[r_old:CO_MENTIONED]->(other:Entity)" in cypher and "MERGE (survivor)-[r_new:CO_MENTIONED]" in cypher:
            n = self._repoint("CO_MENTIONED", kw["loser_id"], kw["survivor_id"], "src", limit, "weight")
            return _Result([{"processed": n}])
        if "MERGE (other)-[r_new:CO_MENTIONED]->(survivor)" in cypher:
            n = self._repoint("CO_MENTIONED", kw["loser_id"], kw["survivor_id"], "dst", limit, "weight")
            return _Result([{"processed": n}])
        if "[r_old:SIMILAR_TO]->(other:Entity)" in cypher and "MERGE (survivor)-[r_new:SIMILAR_TO]" in cypher:
            n = self._repoint("SIMILAR_TO", kw["loser_id"], kw["survivor_id"], "src", limit, "score")
            return _Result([{"processed": n}])
        if "MERGE (other)-[r_new:SIMILAR_TO]->(survivor)" in cypher:
            n = self._repoint("SIMILAR_TO", kw["loser_id"], kw["survivor_id"], "dst", limit, "score")
            return _Result([{"processed": n}])
        if "[r_old:IN_COMMUNITY]" in cypher:
            n = self._repoint("IN_COMMUNITY", kw["loser_id"], kw["survivor_id"], "src", limit, "plain")
            return _Result([{"processed": n}])
        if "[r_old:HAS_CONTRADICTION]" in cypher:
            n = self._repoint("HAS_CONTRADICTION", kw["loser_id"], kw["survivor_id"], "src", limit, "plain")
            return _Result([{"processed": n}])
        if "[r_old:ENRICHED_FROM]" in cypher:
            n = self._repoint("ENRICHED_FROM", kw["loser_id"], kw["survivor_id"], "src", limit, "plain", source_keyed=True)
            return _Result([{"processed": n}])
        if "[r_old:HAS_FACT]" in cypher:
            n = self._repoint("HAS_FACT", kw["loser_id"], kw["survivor_id"], "src", limit, "plain")
            return _Result([{"processed": n}])
        if "[r_old:FACT_OBJECT]" in cypher:
            n = self._repoint("FACT_OBJECT", kw["loser_id"], kw["survivor_id"], "dst", limit, "plain")
            return _Result([{"processed": n}])

        # --- writes -------------------------------------------------
        if "MERGE (e:Entity {canonical_id: $canonical_id})" in cypher and "e.mention_count = 0" in cypher:
            cid = kw["canonical_id"]
            if cid not in g.entities:
                g.add_entity(cid, kw["name"], kw["entity_type"], mentions=0)
            return _Result([])
        if "MERGE (e:Entity {canonical_id: $canonical_id})" in cypher and "e.mention_count = $mention_count" in cypher:
            cid = kw["canonical_id"]  # restore (unmerge)
            g.entities[cid] = {
                "canonical_id": cid, "name": kw["name"],
                "entity_type": kw["entity_type"], "mention_count": kw["mention_count"],
            }
            return _Result([])
        if "SET survivor.mention_count = coalesce(survivor.mention_count, 0) + $delta" in cypher:
            g.entities[kw["survivor_id"]]["mention_count"] += kw["delta"]
            return _Result([])
        if "coalesce(survivor.mention_count, 0) - $delta" in cypher:  # decrement
            surv = g.entities[kw["survivor_id"]]
            surv["mention_count"] = max(0, surv.get("mention_count", 0) - kw["delta"])
            return _Result([])
        if "MERGE (t:MergedEntity {canonical_id: $loser_id})" in cypher:
            g.tombstones[kw["loser_id"]] = {
                "name": kw["name"], "entity_type": kw["entity_type"],
                "mention_count": kw["mention_count"], "merged_at": kw["merged_at"],
                "merge_confidence": kw["merge_confidence"], "merge_method": kw["merge_method"],
                "mentions_snapshot": kw["mentions_snapshot"],
            }
            g.merged_into[kw["loser_id"]] = kw["survivor_id"]
            return _Result([])
        if "MATCH (loser:Entity {canonical_id: $loser_id}) DETACH DELETE loser" in cypher:
            loser = kw["loser_id"]
            g.entities.pop(loser, None)
            g.edges = [e for e in g.edges if e["src"] != loser and e["dst"] != loser]
            return _Result([])
        if "MERGE (a)-[m:MENTIONS]->(loser)" in cypher:  # restore mentions
            for b in kw["batch"]:
                g.add_edge("MENTIONS", b["art_id"], kw["loser_id"],
                           confidence=b.get("confidence"), chunk_ids=b.get("chunk_ids"),
                           created_at=b.get("created_at"))
            return _Result([])
        if "MATCH (t:MergedEntity {canonical_id: $loser_id})" in cypher and "DETACH DELETE t" in cypher:
            g.tombstones.pop(kw["loser_id"], None)
            g.merged_into.pop(kw["loser_id"], None)
            return _Result([])

        return _Result([])


class _FakeDriver:
    def __init__(self, g: _Graph) -> None:
        self._g = g

    def session(self, **_: Any) -> _FakeSession:
        return _FakeSession(self._g)


# ---------------------------------------------------------------------------
# merge_entities — edge re-point inventory
# ---------------------------------------------------------------------------


def _two_entities(g: _Graph) -> None:
    g.add_entity("person:jp", "Jerome Powell", "PERSON", mentions=3)
    g.add_entity("person:powell", "Powell", "PERSON", mentions=1)


class TestMergeEdgeRepoint:
    def test_mentions_repoint_and_mention_count_summed(self):
        from app.db.neo4j.entity import merge_entities
        g = _Graph()
        _two_entities(g)
        g.add_edge("MENTIONS", "art-1", "person:powell", confidence=0.9, chunk_ids="[]")
        driver = _FakeDriver(g)

        merge_entities(driver, "person:jp", ["person:powell"],
                       survivor_name="Jerome Powell", entity_type="PERSON")

        assert "person:powell" not in g.entities  # loser gone
        assert g.edges_of("MENTIONS", src="art-1", dst="person:jp")  # re-pointed
        assert not g.edges_of("MENTIONS", dst="person:powell")
        assert g.entities["person:jp"]["mention_count"] == 4  # 3 + 1

    def test_co_mentioned_both_directions_and_weight_summed(self):
        from app.db.neo4j.entity import merge_entities
        g = _Graph()
        _two_entities(g)
        g.add_entity("org:fed", "Fed", "ORG")
        # survivor and loser both co-mention org:fed (out) -> weights sum on dedup
        g.add_edge("CO_MENTIONED", "person:jp", "org:fed", weight=2)
        g.add_edge("CO_MENTIONED", "person:powell", "org:fed", weight=5)
        # inbound edge into loser
        g.add_edge("CO_MENTIONED", "org:fed", "person:powell", weight=1)
        driver = _FakeDriver(g)

        merge_entities(driver, "person:jp", ["person:powell"], entity_type="PERSON")

        out = g.edges_of("CO_MENTIONED", src="person:jp", dst="org:fed")
        assert len(out) == 1 and out[0]["props"]["weight"] == 7  # 2 + 5 merged
        assert g.edges_of("CO_MENTIONED", src="org:fed", dst="person:jp")
        assert not g.edges_of("CO_MENTIONED", src="person:powell")
        assert not g.edges_of("CO_MENTIONED", dst="person:powell")

    def test_co_mentioned_self_loop_dropped(self):
        from app.db.neo4j.entity import merge_entities
        g = _Graph()
        _two_entities(g)
        # loser co-mentioned WITH the survivor -> must not become a self-loop
        g.add_edge("CO_MENTIONED", "person:powell", "person:jp", weight=4)
        driver = _FakeDriver(g)

        merge_entities(driver, "person:jp", ["person:powell"], entity_type="PERSON")

        assert not [e for e in g.edges if e["type"] == "CO_MENTIONED" and e["src"] == e["dst"]]

    def test_similar_to_repoint_score_max(self):
        from app.db.neo4j.entity import merge_entities
        g = _Graph()
        _two_entities(g)
        g.add_entity("person:x", "X", "PERSON")
        g.add_edge("SIMILAR_TO", "person:jp", "person:x", score=0.7)
        g.add_edge("SIMILAR_TO", "person:powell", "person:x", score=0.9)
        driver = _FakeDriver(g)

        merge_entities(driver, "person:jp", ["person:powell"], entity_type="PERSON")

        out = g.edges_of("SIMILAR_TO", src="person:jp", dst="person:x")
        assert len(out) == 1 and out[0]["props"]["score"] == 0.9  # max

    def test_in_community_and_contradiction_and_enriched_repoint(self):
        from app.db.neo4j.entity import merge_entities
        g = _Graph()
        _two_entities(g)
        g.add_edge("IN_COMMUNITY", "person:powell", "community:5")
        g.add_edge("HAS_CONTRADICTION", "person:powell", "finding:1", linked_at="t0")
        g.add_edge("ENRICHED_FROM", "person:powell", "ref:1", source="wikipedia", fetched_at="t0")
        driver = _FakeDriver(g)

        merge_entities(driver, "person:jp", ["person:powell"], entity_type="PERSON")

        assert g.edges_of("IN_COMMUNITY", src="person:jp", dst="community:5")
        assert g.edges_of("HAS_CONTRADICTION", src="person:jp", dst="finding:1")
        ef = g.edges_of("ENRICHED_FROM", src="person:jp", dst="ref:1")
        assert ef and ef[0]["props"]["source"] == "wikipedia"

    def test_has_fact_and_fact_object_repoint(self):
        """HAS_FACT/FACT_OBJECT re-point against the m0004/m0006 :Fact schema
        (zero facts in production — this fixture proves future-proofness)."""
        from app.db.neo4j.entity import merge_entities
        g = _Graph()
        _two_entities(g)
        g.add_entity("org:tesla", "Tesla", "ORG")
        # (loser)-[:HAS_FACT]->(:Fact) and (:Fact)-[:FACT_OBJECT]->(loser)
        g.add_edge("HAS_FACT", "person:powell", "fact:powell|ceo_of|tesla")
        g.add_edge("FACT_OBJECT", "fact:musk|mentored|powell", "person:powell")
        driver = _FakeDriver(g)

        merge_entities(driver, "person:jp", ["person:powell"], entity_type="PERSON")

        assert g.edges_of("HAS_FACT", src="person:jp", dst="fact:powell|ceo_of|tesla")
        assert g.edges_of("FACT_OBJECT", src="fact:musk|mentored|powell", dst="person:jp")
        assert not g.edges_of("HAS_FACT", src="person:powell")
        assert not g.edges_of("FACT_OBJECT", dst="person:powell")


# ---------------------------------------------------------------------------
# merge_entities <-> reconcile_fact_subjects wiring (aggregation only — the
# actual :Fact property-fold semantics are exercised directly below against
# _FactFakeDriver, since _Graph/_FakeSession models edges, not :Fact node
# properties).
# ---------------------------------------------------------------------------


class TestMergeFactReconcileWiring:
    def test_merge_entities_aggregates_fact_reconcile_counts(self):
        from unittest.mock import patch

        from app.db.neo4j.entity import merge_entities
        g = _Graph()
        g.add_entity("person:jp", "Jerome Powell", "PERSON", mentions=1)
        g.add_entity("person:a", "A", "PERSON", mentions=1)
        g.add_entity("person:b", "B", "PERSON", mentions=1)
        driver = _FakeDriver(g)

        per_loser = {
            "person:a": {"subjects_repointed": 2, "facts_folded": 1, "objects_repointed": 0},
            "person:b": {"subjects_repointed": 1, "facts_folded": 0, "objects_repointed": 1},
        }

        def _fake_reconcile(session, survivor_id, loser_ids, *, chunk_size=None):
            assert survivor_id == "person:jp"
            return per_loser[loser_ids[0]]

        with patch(
            "app.db.neo4j.entity.reconcile_fact_subjects", side_effect=_fake_reconcile
        ) as mock_reconcile:
            result = merge_entities(
                driver, "person:jp", ["person:a", "person:b"], entity_type="PERSON",
            )

        assert result["fact_reconcile"] == {
            "subjects_repointed": 3, "facts_folded": 1, "objects_repointed": 1,
        }
        assert mock_reconcile.call_count == 2

    def test_merge_entities_calls_reconcile_once_per_loser_with_singleton_list(self):
        from unittest.mock import patch

        from app.db.neo4j.entity import merge_entities
        g = _Graph()
        g.add_entity("person:jp", "Jerome Powell", "PERSON", mentions=1)
        g.add_entity("person:powell", "Powell", "PERSON", mentions=1)
        driver = _FakeDriver(g)

        with patch(
            "app.db.neo4j.entity.reconcile_fact_subjects",
            return_value={"subjects_repointed": 0, "facts_folded": 0, "objects_repointed": 0},
        ) as mock_reconcile:
            merge_entities(driver, "person:jp", ["person:powell"], entity_type="PERSON")

        mock_reconcile.assert_called_once()
        args, kwargs = mock_reconcile.call_args
        assert args[1] == "person:jp"
        assert args[2] == ["person:powell"]


# ---------------------------------------------------------------------------
# reconcile_fact_subjects — :Fact property fold semantics (dedicated fake:
# :Fact nodes as dicts with real properties, since _Graph/_FakeSession above
# models edges only).
# ---------------------------------------------------------------------------


class _FactGraph:
    """Models :Fact node properties + provenance/FACT_OBJECT edges, mirroring
    reconcile_fact_subjects's three Cypher statements (chunked, collision
    detected via existing target uid) so assertions are semantic."""

    def __init__(self) -> None:
        self.facts: dict[str, dict[str, Any]] = {}          # uid -> props
        self.provenance: set[tuple[str, str]] = set()        # (artifact_id, uid)
        self.fact_object: set[tuple[str, str]] = set()       # (uid, object_entity_id)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def add_fact(
        self, uid: str, *, subject_id: str, fact_key: str, object_id: str | None = None,
        valid_from: str | None = "", valid_to: str | None = None,
        invalid_at: str | None = None, source: str = "extraction",
    ) -> None:
        self.facts[uid] = {
            "subject_id": subject_id, "object_id": object_id, "fact_key": fact_key,
            "valid_from": valid_from, "valid_to": valid_to, "invalid_at": invalid_at,
            "source": source,
        }

    def add_provenance(self, artifact_id: str, uid: str) -> None:
        self.provenance.add((artifact_id, uid))

    def add_fact_object(self, uid: str, object_entity_id: str) -> None:
        self.fact_object.add((uid, object_entity_id))


def _earlier_nonempty(a: str | None, b: str | None) -> str | None:
    if not a:
        return b
    if not b:
        return a
    return a if a < b else b


class _FactFakeSession:
    """Interprets exactly the three reconcile_fact_subjects Cypher statements
    (dispatched by a unique substring each) against a ``_FactGraph``."""

    def __init__(self, g: _FactGraph) -> None:
        self._g = g

    def __enter__(self) -> "_FactFakeSession":
        return self

    def __exit__(self, *_: Any) -> Literal[False]:
        return False

    def run(self, cypher: str, **kw: Any) -> _Result:
        self._g.calls.append((cypher, kw))
        g = self._g
        limit = kw.get("limit", 10**9)
        survivor_id = kw["survivor_id"]
        loser_ids = kw["loser_ids"]

        if "SET f.subject_id = $survivor_id, f.uid = new_uid" in cypher:
            processed = 0
            for uid in list(g.facts):
                if processed >= limit:
                    break
                props = g.facts.get(uid)
                if props is None or props["subject_id"] not in loser_ids:
                    continue
                new_uid = f"{survivor_id}|{props['fact_key']}"
                if new_uid in g.facts:
                    continue  # collision — handled by the fold statement
                del g.facts[uid]
                props["subject_id"] = survivor_id
                g.facts[new_uid] = props
                g.provenance = {(a, new_uid if u == uid else u) for a, u in g.provenance}
                g.fact_object = {(new_uid if u == uid else u, o) for u, o in g.fact_object}
                processed += 1
            return _Result([{"processed": processed}])

        if "DETACH DELETE f" in cypher:
            processed = 0
            for uid in list(g.facts):
                if processed >= limit:
                    break
                f = g.facts.get(uid)
                if f is None or f["subject_id"] not in loser_ids:
                    continue
                new_uid = f"{survivor_id}|{f['fact_key']}"
                if new_uid not in g.facts or new_uid == uid:
                    continue  # no collision — handled by the no-collision statement
                gprops = g.facts[new_uid]
                gprops["valid_from"] = _earlier_nonempty(f["valid_from"], gprops["valid_from"])
                gprops["valid_to"] = (
                    None if (f["valid_to"] is None or gprops["valid_to"] is None)
                    else max(f["valid_to"], gprops["valid_to"])
                )
                gprops["invalid_at"] = (
                    None if (f["invalid_at"] is None or gprops["invalid_at"] is None)
                    else max(f["invalid_at"], gprops["invalid_at"])
                )
                if f["source"] == "verification" or gprops["source"] == "verification":
                    gprops["source"] = "verification"
                g.provenance = {(a, new_uid if u == uid else u) for a, u in g.provenance}
                g.fact_object = {(new_uid if u == uid else u, o) for u, o in g.fact_object}
                del g.facts[uid]
                processed += 1
            return _Result([{"processed": processed}])

        if "SET f.object_id = $survivor_id" in cypher:
            processed = 0
            for uid, props in g.facts.items():
                if processed >= limit:
                    break
                if props.get("object_id") in loser_ids:
                    props["object_id"] = survivor_id
                    processed += 1
            return _Result([{"processed": processed}])

        return _Result([])


class _FactFakeDriver:
    def __init__(self, g: _FactGraph) -> None:
        self._g = g

    def session(self, **_: Any) -> _FactFakeSession:
        return _FactFakeSession(self._g)


class TestReconcileFactSubjects:
    def test_no_collision_repoints_subject_and_uid(self):
        from app.db.neo4j.facts import reconcile_fact_subjects
        g = _FactGraph()
        g.add_fact(
            "person:powell|ceo_of|fed", subject_id="person:powell",
            fact_key="ceo_of|fed", valid_from="2026-01-01",
        )
        driver = _FactFakeDriver(g)

        result = reconcile_fact_subjects(driver, "person:jp", ["person:powell"])

        assert result == {"subjects_repointed": 1, "facts_folded": 0, "objects_repointed": 0}
        assert "person:jp|ceo_of|fed" in g.facts
        assert "person:powell|ceo_of|fed" not in g.facts
        assert g.facts["person:jp|ceo_of|fed"]["subject_id"] == "person:jp"

    def test_collision_folds_interval_open_beats_closed(self):
        from app.db.neo4j.facts import reconcile_fact_subjects
        g = _FactGraph()
        g.add_fact(
            "person:powell|role", subject_id="person:powell", fact_key="role",
            valid_from="2026-02-01", valid_to=None, invalid_at=None, source="extraction",
        )
        g.add_fact(
            "person:jp|role", subject_id="person:jp", fact_key="role",
            valid_from="2026-01-01", valid_to="2026-03-01", invalid_at="2026-03-01",
            source="extraction",
        )
        driver = _FactFakeDriver(g)

        result = reconcile_fact_subjects(driver, "person:jp", ["person:powell"])

        assert result == {"subjects_repointed": 0, "facts_folded": 1, "objects_repointed": 0}
        assert "person:powell|role" not in g.facts
        survivor_fact = g.facts["person:jp|role"]
        assert survivor_fact["valid_from"] == "2026-01-01"   # earlier non-empty wins
        assert survivor_fact["valid_to"] is None              # open beats closed
        assert survivor_fact["invalid_at"] is None             # open beats closed

    def test_collision_folds_source_verification_wins(self):
        from app.db.neo4j.facts import reconcile_fact_subjects
        g = _FactGraph()
        g.add_fact(
            "person:powell|role", subject_id="person:powell", fact_key="role",
            valid_from="2026-01-01", source="verification",
        )
        g.add_fact(
            "person:jp|role", subject_id="person:jp", fact_key="role",
            valid_from="2026-01-01", source="extraction",
        )
        driver = _FactFakeDriver(g)

        reconcile_fact_subjects(driver, "person:jp", ["person:powell"])

        assert g.facts["person:jp|role"]["source"] == "verification"

    def test_collision_repoints_provenance_and_fact_object_edges(self):
        from app.db.neo4j.facts import reconcile_fact_subjects
        g = _FactGraph()
        g.add_fact(
            "person:powell|role", subject_id="person:powell", fact_key="role",
            valid_from="2026-01-01",
        )
        g.add_fact(
            "person:jp|role", subject_id="person:jp", fact_key="role",
            valid_from="2026-01-01",
        )
        g.add_provenance("art-1", "person:powell|role")
        g.add_fact_object("person:powell|role", "org:fed")
        driver = _FactFakeDriver(g)

        reconcile_fact_subjects(driver, "person:jp", ["person:powell"])

        assert ("art-1", "person:jp|role") in g.provenance
        assert ("person:jp|role", "org:fed") in g.fact_object
        assert not any(u == "person:powell|role" for _, u in g.provenance)
        assert not any(u == "person:powell|role" for u, _ in g.fact_object)

    def test_object_id_repoint_is_property_only_and_warns(self, caplog):
        import logging

        from app.db.neo4j.facts import reconcile_fact_subjects
        g = _FactGraph()
        g.add_fact(
            "person:musk|mentored|powell", subject_id="person:musk",
            fact_key="mentored|person:powell", object_id="person:powell",
            valid_from="2026-01-01",
        )
        driver = _FactFakeDriver(g)

        with caplog.at_level(logging.WARNING, logger="ai-companion.graph.facts"):
            result = reconcile_fact_subjects(driver, "person:jp", ["person:powell"])

        assert result["objects_repointed"] == 1
        fact = g.facts["person:musk|mentored|powell"]
        assert fact["object_id"] == "person:jp"
        assert fact["fact_key"] == "mentored|person:powell"  # untouched
        assert any("binary" in r.message for r in caplog.records)

    def test_no_facts_touched_no_warning(self, caplog):
        import logging

        from app.db.neo4j.facts import reconcile_fact_subjects
        g = _FactGraph()
        g.add_fact(
            "person:powell|ceo_of|fed", subject_id="person:powell",
            fact_key="ceo_of|fed", valid_from="2026-01-01",
        )
        driver = _FactFakeDriver(g)

        with caplog.at_level(logging.WARNING, logger="ai-companion.graph.facts"):
            reconcile_fact_subjects(driver, "person:jp", ["person:powell"])

        assert not any("binary" in r.message for r in caplog.records)

    def test_idempotent_rerun_is_noop(self):
        from app.db.neo4j.facts import reconcile_fact_subjects
        g = _FactGraph()
        g.add_fact(
            "person:powell|ceo_of|fed", subject_id="person:powell",
            fact_key="ceo_of|fed", valid_from="2026-01-01",
        )
        driver = _FactFakeDriver(g)

        reconcile_fact_subjects(driver, "person:jp", ["person:powell"])
        second = reconcile_fact_subjects(driver, "person:jp", ["person:powell"])

        assert second == {"subjects_repointed": 0, "facts_folded": 0, "objects_repointed": 0}

    def test_chunked_repoint_drains_across_batches(self):
        from app.db.neo4j.facts import reconcile_fact_subjects
        g = _FactGraph()
        for i in range(5):
            g.add_fact(
                f"person:powell|fact{i}", subject_id="person:powell",
                fact_key=f"fact{i}", valid_from="2026-01-01",
            )
        driver = _FactFakeDriver(g)

        result = reconcile_fact_subjects(driver, "person:jp", ["person:powell"], chunk_size=2)

        assert result["subjects_repointed"] == 5
        repoint_calls = [
            c for c, _ in g.calls
            if "SET f.subject_id = $survivor_id, f.uid = new_uid" in c
        ]
        assert len(repoint_calls) > 1  # proves chunking, not one shot

    def test_empty_loser_ids_is_noop(self):
        from app.db.neo4j.facts import reconcile_fact_subjects
        g = _FactGraph()
        driver = _FactFakeDriver(g)
        result = reconcile_fact_subjects(driver, "person:jp", [])
        assert result == {"subjects_repointed": 0, "facts_folded": 0, "objects_repointed": 0}
        assert g.calls == []


# ---------------------------------------------------------------------------
# Provenance + reversibility
# ---------------------------------------------------------------------------


class TestMergeProvenance:
    def test_tombstone_and_merged_into_recorded(self):
        from app.db.neo4j.entity import MERGE_METHOD_EMBEDDING_ADJUDICATED, merge_entities
        g = _Graph()
        _two_entities(g)
        g.add_edge("MENTIONS", "art-1", "person:powell", confidence=0.8, chunk_ids="[]", created_at="t0")
        driver = _FakeDriver(g)

        merge_entities(driver, "person:jp", ["person:powell"], entity_type="PERSON",
                       merge_method=MERGE_METHOD_EMBEDDING_ADJUDICATED, merge_confidence=0.88)

        tomb = g.tombstones["person:powell"]
        assert tomb["merge_method"] == MERGE_METHOD_EMBEDDING_ADJUDICATED
        assert tomb["merge_confidence"] == 0.88
        assert g.merged_into["person:powell"] == "person:jp"
        snap = json.loads(tomb["mentions_snapshot"])
        assert snap and snap[0]["art_id"] == "art-1"


class TestUnmerge:
    def test_unmerge_restores_identity_and_mentions(self):
        from app.db.neo4j.entity import merge_entities, unmerge_entity
        g = _Graph()
        _two_entities(g)
        g.add_edge("MENTIONS", "art-1", "person:powell", confidence=0.8, chunk_ids="[c1]", created_at="t0")
        driver = _FakeDriver(g)

        merge_entities(driver, "person:jp", ["person:powell"], entity_type="PERSON")
        assert "person:powell" not in g.entities
        assert g.entities["person:jp"]["mention_count"] == 4

        result = unmerge_entity(driver, "person:powell")

        assert result["status"] == "restored"
        assert "person:powell" in g.entities  # identity restored
        assert g.entities["person:powell"]["name"] == "Powell"
        assert g.entities["person:powell"]["mention_count"] == 1
        assert g.edges_of("MENTIONS", src="art-1", dst="person:powell")  # mentions restored
        assert g.entities["person:jp"]["mention_count"] == 3  # decremented back
        assert "person:powell" not in g.tombstones  # tombstone consumed

    def test_unmerge_without_provenance_is_noop(self):
        from app.db.neo4j.entity import unmerge_entity
        g = _Graph()
        driver = _FakeDriver(g)
        result = unmerge_entity(driver, "person:nope")
        assert result["status"] == "no_provenance"


class TestUnmergeFactWarning:
    def test_unmerge_warns_when_survivor_has_facts(self):
        from unittest.mock import patch

        from app.db.neo4j.entity import merge_entities, unmerge_entity
        g = _Graph()
        _two_entities(g)
        g.add_edge(
            "MENTIONS", "art-1", "person:powell",
            confidence=0.8, chunk_ids="[]", created_at="t0",
        )
        driver = _FakeDriver(g)

        merge_entities(driver, "person:jp", ["person:powell"], entity_type="PERSON")
        # A fact reconciled onto the survivor by an earlier (unrelated) merge —
        # fact reconciliation is one-way, so unmerge should flag this.
        g.add_edge("HAS_FACT", "person:jp", "fact:jp|ceo_of|fed")

        with patch("app.db.neo4j.entity.logger") as mock_logger:
            unmerge_entity(driver, "person:powell")

        assert mock_logger.warning.called
        warned = [str(c.args[0] % c.args[1:]) for c in mock_logger.warning.call_args_list]
        assert any("carries" in m and ":Fact" in m for m in warned)

    def test_unmerge_no_warning_when_survivor_has_no_facts(self):
        from unittest.mock import patch

        from app.db.neo4j.entity import merge_entities, unmerge_entity
        g = _Graph()
        _two_entities(g)
        g.add_edge(
            "MENTIONS", "art-1", "person:powell",
            confidence=0.8, chunk_ids="[]", created_at="t0",
        )
        driver = _FakeDriver(g)

        merge_entities(driver, "person:jp", ["person:powell"], entity_type="PERSON")

        with patch("app.db.neo4j.entity.logger") as mock_logger:
            unmerge_entity(driver, "person:powell")

        assert not mock_logger.warning.called


# ---------------------------------------------------------------------------
# Chunked UNWIND
# ---------------------------------------------------------------------------


class TestChunkedRepoint:
    def test_mentions_repoint_is_batched_above_chunk_size(self):
        from app.db.neo4j.entity import merge_entities
        g = _Graph()
        _two_entities(g)
        for i in range(5):
            g.add_edge("MENTIONS", f"art-{i}", "person:powell", confidence=0.5, chunk_ids="[]")
        driver = _FakeDriver(g)

        merge_entities(driver, "person:jp", ["person:powell"], entity_type="PERSON", chunk_size=2)

        # All 5 re-pointed…
        assert len(g.edges_of("MENTIONS", dst="person:jp")) == 5
        assert not g.edges_of("MENTIONS", dst="person:powell")
        # …across MULTIPLE batched calls (proves the UNWIND is chunked, not one shot).
        mentions_calls = [
            c for c, _ in g.calls if "MERGE (a)-[m_new:MENTIONS]->(survivor)" in c
        ]
        assert len(mentions_calls) > 1, (
            f"expected chunked MENTIONS re-point, got {len(mentions_calls)} call(s)"
        )


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------


def _ent(cid: str, name: str, etype: str, emb: list[float]) -> dict[str, Any]:
    return {"canonical_id": cid, "name": name, "entity_type": etype, "embedding": emb}


class TestCandidateGeneration:
    def test_auto_and_adjudicate_banding(self):
        from core.agents.entity_resolution import generate_merge_candidates
        entities = [
            _ent("person:a", "Jerome Powell", "PERSON", [1.0, 0.0]),
            _ent("person:b", "J. Powell", "PERSON", [1.0, 0.0]),       # cos 1.0 -> auto
            _ent("person:c", "Powell", "PERSON", [0.90, 0.436]),        # cos ~0.90 -> adjudicate
        ]
        pairs = generate_merge_candidates(
            entities, auto_threshold=0.94, adjudicate_floor=0.86
        )
        bands = {(p.a_id, p.b_id): p.band for p in pairs}
        assert bands[("person:a", "person:b")] == "auto"
        assert bands[("person:a", "person:c")] == "adjudicate"

    def test_below_floor_dropped(self):
        from core.agents.entity_resolution import generate_merge_candidates
        entities = [
            _ent("person:a", "A", "PERSON", [1.0, 0.0]),
            _ent("person:b", "B", "PERSON", [0.0, 1.0]),  # orthogonal -> cos 0
        ]
        pairs = generate_merge_candidates(entities, auto_threshold=0.94, adjudicate_floor=0.86)
        assert pairs == []

    def test_cross_type_never_paired(self):
        from core.agents.entity_resolution import generate_merge_candidates
        entities = [
            _ent("person:powell", "Powell", "PERSON", [1.0, 0.0]),
            _ent("org:powell", "Powell", "ORG", [1.0, 0.0]),  # identical vec, different type
        ]
        pairs = generate_merge_candidates(entities, auto_threshold=0.94, adjudicate_floor=0.86)
        assert pairs == []

    def test_entities_without_embedding_skipped(self):
        from core.agents.entity_resolution import generate_merge_candidates
        entities = [
            _ent("person:a", "A", "PERSON", [1.0, 0.0]),
            {"canonical_id": "person:b", "name": "B", "entity_type": "PERSON", "embedding": None},
        ]
        pairs = generate_merge_candidates(entities, auto_threshold=0.94, adjudicate_floor=0.86)
        assert pairs == []


# ---------------------------------------------------------------------------
# LLM adjudication routing
# ---------------------------------------------------------------------------


class TestAdjudication:
    @pytest.mark.asyncio
    async def test_adjudicator_confirms_merge_only_for_merge_verdicts(self):
        from core.agents.entity_resolution import MergePair, adjudicate_merge_pairs

        captured: dict[str, Any] = {}

        async def _fake_llm(messages, **kwargs):
            captured["stage"] = kwargs.get("stage")
            # index 0 -> merge, index 1 -> keep
            return json.dumps({"results": [
                {"index": 0, "decision": "merge"},
                {"index": 1, "decision": "keep"},
            ]})

        pairs = [
            MergePair("person:a", "person:b", "A", "B", "PERSON", 0.9, "adjudicate"),
            MergePair("person:c", "person:d", "C", "D", "PERSON", 0.88, "adjudicate"),
        ]
        confirmed = await adjudicate_merge_pairs(
            pairs, llm_call=_fake_llm, max_pairs=10, batch_size=5
        )
        assert [p.a_id for p in confirmed] == ["person:a"]
        assert captured["stage"] == "entity_merge_adjudication"

    @pytest.mark.asyncio
    async def test_adjudication_bounded_by_max_pairs(self):
        from core.agents.entity_resolution import MergePair, adjudicate_merge_pairs

        seen: list[int] = []

        async def _fake_llm(messages, **kwargs):
            # count how many pairs were presented across calls
            seen.append(messages[-1]["content"].count("type="))
            return json.dumps({"results": []})

        pairs = [
            MergePair(f"person:a{i}", f"person:b{i}", "A", "B", "PERSON", 0.9, "adjudicate")
            for i in range(20)
        ]
        await adjudicate_merge_pairs(pairs, llm_call=_fake_llm, max_pairs=3, batch_size=5)
        assert sum(seen) == 3  # never adjudicated more than the cap

    @pytest.mark.asyncio
    async def test_bad_json_yields_no_merges(self):
        from core.agents.entity_resolution import MergePair, adjudicate_merge_pairs

        async def _bad_llm(messages, **kwargs):
            return "not json at all"

        pairs = [MergePair("person:a", "person:b", "A", "B", "PERSON", 0.9, "adjudicate")]
        confirmed = await adjudicate_merge_pairs(pairs, llm_call=_bad_llm, max_pairs=10, batch_size=5)
        assert confirmed == []


# ---------------------------------------------------------------------------
# Embedding-resolution orchestration (dry-run + flag gate)
# ---------------------------------------------------------------------------


class _EmbedResolutionDriver:
    """Fake driver returning entities-with-embeddings for the fetch query and
    recording every write so a dry-run can be asserted to write nothing."""

    def __init__(self, entity_rows: list[dict[str, Any]]) -> None:
        self._rows = entity_rows
        self.write_calls: list[str] = []
        self._graph = _Graph()

    def session(self, **_: Any) -> Any:
        outer = self

        class _S:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): return False

            def run(self_inner, cypher: str, **kw: Any):
                if "e.embedding AS embedding" in cypher:
                    return _Result(list(outer._rows))
                outer.write_calls.append(cypher)
                return _Result([])

        return _S()


class TestEmbeddingResolutionOrchestration:
    def test_dry_run_writes_nothing(self, monkeypatch):
        import config.settings as s
        monkeypatch.setattr(s, "ENTITY_RESOLUTION_EMBED", True)
        from scripts.merge_entity_aliases import run_embedding_resolution

        rows = [
            {"canonical_id": "person:a", "name": "Jerome Powell", "entity_type": "PERSON",
             "mention_count": 3, "primary_domain": "finance", "embedding": json.dumps([1.0, 0.0])},
            {"canonical_id": "person:b", "name": "J. Powell", "entity_type": "PERSON",
             "mention_count": 1, "primary_domain": "finance", "embedding": json.dumps([1.0, 0.0])},
        ]
        driver = _EmbedResolutionDriver(rows)

        result = run_embedding_resolution(driver, dry_run=True)

        assert result["dry_run"] is True
        assert result["merge_clusters"] == 1      # a+b cluster identified
        assert result["merged_clusters"] == 0     # but nothing applied
        assert driver.write_calls == []           # zero writes in dry-run

    def test_flag_off_is_noop(self, monkeypatch):
        import config.settings as s
        monkeypatch.setattr(s, "ENTITY_RESOLUTION_EMBED", False)
        from scripts.merge_entity_aliases import run_embedding_resolution

        driver = _EmbedResolutionDriver([])
        result = run_embedding_resolution(driver, dry_run=True)
        assert result["skipped"] == "disabled"
