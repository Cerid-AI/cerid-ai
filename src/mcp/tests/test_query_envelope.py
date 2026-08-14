from app.models.query_envelope import QueryEnvelope, SourceItem
from core.agents.query_agent import _format_chroma_result, assemble_context


def _src(
    name: str, artifact_id: str = "", source_type: str = "kb", relevance: float = 0.5
) -> SourceItem:
    return SourceItem(
        content=f"c-{name}",
        relevance=relevance,
        artifact_id=artifact_id,
        filename=name,
        source_type=source_type,
        domain="general",
        chunk_id="",
        collection="",
    )


def test_envelope_shape_invariant_len():
    """results == flatten(source_breakdown) always."""
    env = QueryEnvelope(
        kb=[_src("a.md"), _src("b.md")],
        memory=[_src("mem-1", source_type="memory")],
        external=[_src("w", source_type="external")],
    )
    out = env.to_dict()
    total = sum(len(v) for v in out["source_breakdown"].values())
    assert len(out["results"]) == total
    assert len(out["sources"]) == total


def test_envelope_degraded_path_preserves_external():
    """Budget-exhaust with external completions still exposes external in all three views."""
    env = QueryEnvelope(external=[_src("w", source_type="external")])
    env.mark_degraded(budget_seconds=10.0, reason="test")
    out = env.to_dict()
    assert out["budget_exceeded"] is True
    assert out["strategy"] == "degraded_budget_exhausted"
    assert len(out["results"]) == 1
    assert len(out["source_breakdown"]["external"]) == 1
    # E1 CR-032: source_status is not serialized (internal statuses still drive
    # mark_degraded — external bucket remains populated above).
    assert "source_status" not in out
    assert env.external_status == "ok"


def test_envelope_empty_when_nothing_ran():
    env = QueryEnvelope()
    env.mark_degraded(budget_seconds=10.0, reason="test")
    out = env.to_dict()
    assert out["results"] == []
    assert out["source_breakdown"] == {"kb": [], "memory": [], "external": []}
    assert "source_status" not in out
    assert env.kb_status == "timeout"
    assert env.memory_status == "timeout"
    assert env.external_status == "timeout"


def test_envelope_merge_external_post_degrade():
    """Late external results can be merged after mark_degraded — the common
    real-world case where the gate expired but the task already finished."""
    env = QueryEnvelope()
    env.mark_degraded(budget_seconds=10.0, reason="test")
    env.merge_external([_src("w", source_type="external", relevance=0.42)])
    out = env.to_dict()
    assert len(out["results"]) == 1
    assert out["source_breakdown"]["external"][0]["relevance"] == 0.42
    assert "source_status" not in out
    assert env.external_status == "ok"


def test_envelope_round_trip_legacy():
    before = QueryEnvelope(
        kb=[_src("a.md")], external=[_src("w", source_type="external")]
    )
    before.mark_degraded(budget_seconds=10.0, reason="x")
    d1 = before.to_dict()
    after = QueryEnvelope.from_legacy_result(d1)
    d2 = after.to_dict()
    d1.pop("timestamp")
    d2.pop("timestamp")
    assert d1 == d2


def test_envelope_round_trip_preserves_informational_keys():
    """from_legacy_result → to_dict must not drop keys it does not model.

    kb-idle-zero diagnosis: the CRAG external merge rebuilt the envelope via
    this round trip and every informational key vanished — the response
    reported domains_searched=[] after searching all 22 domains, and the
    Knowledge Console lost its timing chip (_timings / execution_time_ms).
    """
    legacy = {
        "context": "ctx",
        "sources": [],
        "results": [],
        "confidence": 0.7,
        "domains_searched": ["mail", "general"],
        "surface_route": {"intent": "personal_context"},
        "_timings": {"vector_search": 1.2},
        "execution_time_ms": 1234,
        "total_results": 0,
        "token_budget_used": 10,
        "graph_results": 0,
    }
    out = QueryEnvelope.from_legacy_result(legacy).to_dict()
    assert out["domains_searched"] == ["mail", "general"]
    assert out["surface_route"] == {"intent": "personal_context"}
    assert out["_timings"] == {"vector_search": 1.2}
    assert out["execution_time_ms"] == 1234
    # Extras can never shadow envelope-owned keys (confidence is recomputed
    # from the result pool by the envelope, so it is owned, not passed through).
    assert out["context"] == "ctx"
    assert out["confidence"] == 0.0


# ---------------------------------------------------------------------------
# RAG Phase 1.1 — provenance spine on the KB vector path
# ---------------------------------------------------------------------------


def test_format_chroma_result_kb_source_type():
    """KB chunks (no pack_id) get source_type='kb'."""
    res = _format_chroma_result(
        content="hello",
        relevance=0.9,
        chunk_id="c1",
        domain="general",
        metadata={"artifact_id": "a1", "created_at": "2026-01-02"},
    )
    assert res["source_type"] == "kb"
    assert res["pack_id"] == ""


def test_format_chroma_result_created_at_from_metadata():
    """created_at threads straight from chunk metadata when present."""
    res = _format_chroma_result(
        content="hello",
        relevance=0.5,
        chunk_id="c1",
        domain="general",
        metadata={"created_at": "2026-03-04T05:06:07Z"},
    )
    assert res["created_at"] == "2026-03-04T05:06:07Z"


def test_format_chroma_result_created_at_falls_back_to_ingested_at():
    """Absent created_at, fall back to ingested_at; None when neither exists."""
    res = _format_chroma_result(
        content="hello",
        relevance=0.5,
        chunk_id="c1",
        domain="general",
        metadata={"ingested_at": "2026-02-01"},
    )
    assert res["created_at"] == "2026-02-01"

    bare = _format_chroma_result(
        content="hello",
        relevance=0.5,
        chunk_id="c2",
        domain="general",
        metadata={},
    )
    assert bare["created_at"] is None


def test_format_chroma_result_pack_source_type():
    """A chunk with a truthy pack_id is classified as source_type='pack'."""
    res = _format_chroma_result(
        content="pack chunk",
        relevance=0.7,
        chunk_id="c1",
        domain="research",
        metadata={"pack_id": "pack-xyz", "created_at": "2026-04-05"},
    )
    assert res["source_type"] == "pack"
    assert res["pack_id"] == "pack-xyz"
    assert res["created_at"] == "2026-04-05"


def test_assemble_context_preserves_provenance_fields():
    """assemble_context threads source_type/created_at/pack_id onto sources[]."""
    results = [
        _format_chroma_result(
            content="kb body",
            relevance=0.9,
            chunk_id="c1",
            domain="general",
            metadata={"artifact_id": "a1", "created_at": "2026-01-02"},
        ),
        _format_chroma_result(
            content="pack body",
            relevance=0.8,
            chunk_id="c2",
            domain="research",
            metadata={"artifact_id": "a2", "pack_id": "pk", "created_at": "2026-05-06"},
        ),
    ]
    _, sources, _ = assemble_context(results, max_chars=10000)
    by_artifact = {s["artifact_id"]: s for s in sources}
    assert by_artifact["a1"]["source_type"] == "kb"
    assert by_artifact["a1"]["created_at"] == "2026-01-02"
    assert by_artifact["a1"]["pack_id"] == ""
    assert by_artifact["a2"]["source_type"] == "pack"
    assert by_artifact["a2"]["created_at"] == "2026-05-06"
    assert by_artifact["a2"]["pack_id"] == "pk"


def test_assemble_context_does_not_clobber_existing_source_type():
    """A surface-injected result with its own source_type keeps it."""
    results = [
        {
            "content": "memory body",
            "relevance": 1.0,
            "artifact_id": "m1",
            "filename": "m1",
            "domain": "conversations",
            "chunk_index": 0,
            "source_type": "memory",
            "created_at": "2026-06-07",
        }
    ]
    _, sources, _ = assemble_context(results, max_chars=10000)
    assert sources[0]["source_type"] == "memory"
    assert sources[0]["created_at"] == "2026-06-07"
