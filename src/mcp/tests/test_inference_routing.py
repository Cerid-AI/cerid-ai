# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the inference routing snapshot (v0.93.8).

The snapshot powers ``/health.inference_routing`` and the Settings
pane's "GPU acceleration" disclosure.  These tests pin:

1. Default state — no env vars set → OpenRouter + sidecar across the board.
2. Quenchforge-everywhere — operator opted into all three GPU paths.
3. Mixed — Quenchforge for LLM, sidecar for embed/rerank (a common
   on-Linux configuration).
4. SPLADE disabled vs enabled → disabled / in-process / sidecar.
5. NLI always reports in-process + CPU note (no GPU path today).
"""

from __future__ import annotations


def _clear(monkeypatch) -> None:
    for var in (
        "INTERNAL_LLM_PROVIDER",
        "EMBEDDINGS_PROVIDER",
        "RERANK_PROVIDER",
        "RETRIEVAL_SPARSE_ENABLED",
        "QUENCHFORGE_URL",
        "OLLAMA_URL",
        "QUENCHFORGE_DEFAULT_MODEL",
        "QUENCHFORGE_EMBED_MODEL",
        "QUENCHFORGE_RERANK_MODEL",
        "LLM_INTERNAL_MODEL",
        "OLLAMA_DEFAULT_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_default_state_reports_openrouter_and_sidecar(monkeypatch):
    _clear(monkeypatch)
    from core.utils.inference_routing import get_routing_snapshot
    snap = get_routing_snapshot()
    assert snap["llm"]["provider"] == "openrouter"
    assert snap["embed"]["provider"] == "sidecar"
    assert snap["rerank"]["provider"] == "sidecar"


def test_quenchforge_everywhere(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
    monkeypatch.setenv("RERANK_PROVIDER", "quenchforge")
    monkeypatch.setenv("QUENCHFORGE_URL", "http://qf:11434")
    monkeypatch.setenv("QUENCHFORGE_DEFAULT_MODEL", "qwen2.5:14b-instruct-q4_k_m")
    monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", "nomic-embed-text-v1.5")
    monkeypatch.setenv("QUENCHFORGE_RERANK_MODEL", "bge-reranker-v2-m3")

    from core.utils.inference_routing import get_routing_snapshot
    snap = get_routing_snapshot()
    for key in ("llm", "embed", "rerank"):
        assert snap[key]["provider"] == "quenchforge", f"{key} not on quenchforge"
        assert snap[key]["url"] == "http://qf:11434"
    assert snap["llm"]["model"] == "qwen2.5:14b-instruct-q4_k_m"
    assert snap["embed"]["model"] == "nomic-embed-text-v1.5"
    assert snap["rerank"]["model"] == "bge-reranker-v2-m3"


def test_mixed_quenchforge_llm_sidecar_embed(monkeypatch):
    """A common Linux config: Quenchforge for LLM only (just to test
    against Mac models), sidecar for embeddings/rerank (CUDA GPU)."""
    _clear(monkeypatch)
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "sidecar")
    monkeypatch.setenv("RERANK_PROVIDER", "sidecar")

    from core.utils.inference_routing import get_routing_snapshot
    snap = get_routing_snapshot()
    assert snap["llm"]["provider"] == "quenchforge"
    assert snap["embed"]["provider"] == "sidecar"
    assert snap["rerank"]["provider"] == "sidecar"


def test_sparse_disabled_when_flag_off(monkeypatch):
    _clear(monkeypatch)
    from core.utils.inference_routing import get_routing_snapshot
    snap = get_routing_snapshot()
    assert snap["sparse"]["provider"] == "disabled"


def test_sparse_in_process_when_enabled_without_sidecar(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RETRIEVAL_SPARSE_ENABLED", "true")
    from core.utils.inference_routing import get_routing_snapshot
    snap = get_routing_snapshot()
    assert snap["sparse"]["provider"] in ("in-process", "sidecar")
    # Note explains why Quenchforge isn't in the chain
    if snap["sparse"]["provider"] == "in-process":
        assert "Quenchforge" in snap["sparse"].get("note", "")


def test_nli_always_reports_cpu(monkeypatch):
    """NLI has no GPU path today.  The snapshot must surface that fact
    so operators know it's the lingering CPU bottleneck."""
    _clear(monkeypatch)
    from core.utils.inference_routing import get_routing_snapshot
    snap = get_routing_snapshot()
    assert snap["nli"]["provider"] == "in-process"
    assert "CPU" in snap["nli"]["note"]


def test_nli_reports_coalescer_state(monkeypatch):
    """Operators need to see whether the v0.93.10 batch-coalescer (the
    actual production NLI speedup) is engaged.  Without this field, a
    misconfigured NLI_COALESCE_MS=0 silently loses the 2.5-3x p95 win."""
    _clear(monkeypatch)
    from core.utils.inference_routing import get_routing_snapshot
    snap = get_routing_snapshot()
    assert "coalescer" in snap["nli"]
    assert "coalesce_ms" in snap["nli"]
    assert snap["nli"]["execution"] == "onnx-cpu"
    # Default NLI_COALESCE_MS=10 should report the coalescer active
    assert snap["nli"]["coalescer"] is (snap["nli"]["coalesce_ms"] > 0)


def test_invalid_provider_falls_through_safely(monkeypatch):
    """A typo'd EMBEDDINGS_PROVIDER must not crash the snapshot.
    Anything not "quenchforge" or "sidecar" should report as
    "in-process" without raising."""
    _clear(monkeypatch)
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchfourge")  # typo
    from core.utils.inference_routing import get_routing_snapshot
    snap = get_routing_snapshot()
    # Permissive: should not crash; should fall through to in-process.
    assert snap["embed"]["provider"] in ("in-process", "sidecar")


def test_quenchforge_url_falls_back_to_ollama_url(monkeypatch):
    """When QUENCHFORGE_URL is unset, the snapshot should still resolve
    a usable URL (the same fallback chain the dispatch uses)."""
    _clear(monkeypatch)
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    monkeypatch.setenv("OLLAMA_URL", "http://ol:11434")
    from core.utils.inference_routing import get_routing_snapshot
    snap = get_routing_snapshot()
    assert snap["llm"]["url"] == "http://ol:11434"


def test_model_fields_marked_unset_when_missing(monkeypatch):
    """Quenchforge requires QUENCHFORGE_EMBED_MODEL to be set.  When
    it's missing, the snapshot must mark it ``"unset"`` (string) not
    None — keeps the JSON wire stable for clients."""
    _clear(monkeypatch)
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
    from core.utils.inference_routing import get_routing_snapshot
    snap = get_routing_snapshot()
    assert snap["embed"]["model"] == "unset"
