from app.models.query_envelope import QueryEnvelope, SourceItem


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
    assert out["source_status"]["external"] == "ok"  # external DID finish


def test_envelope_empty_when_nothing_ran():
    env = QueryEnvelope()
    env.mark_degraded(budget_seconds=10.0, reason="test")
    out = env.to_dict()
    assert out["results"] == []
    assert out["source_breakdown"] == {"kb": [], "memory": [], "external": []}
    assert out["source_status"] == {
        "kb": "timeout",
        "memory": "timeout",
        "external": "timeout",
    }


def test_envelope_merge_external_post_degrade():
    """Late external results can be merged after mark_degraded — the common
    real-world case where the gate expired but the task already finished."""
    env = QueryEnvelope()
    env.mark_degraded(budget_seconds=10.0, reason="test")
    env.merge_external([_src("w", source_type="external", relevance=0.42)])
    out = env.to_dict()
    assert len(out["results"]) == 1
    assert out["source_breakdown"]["external"][0]["relevance"] == 0.42
    assert out["source_status"]["external"] == "ok"


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
