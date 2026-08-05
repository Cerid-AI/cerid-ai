# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Bug SW2 — /setup/system-check installed-models cleaning.

The raw Ollama/Quenchforge ``/api/tags`` response includes entries the
setup wizard should never surface as "installed models":

* ``sha256-...`` blob digests (internal content-addressed layers)
* GGUF quant-variant duplicates of a real model
  (``nomic-embed-text-v1.5.Q8_0`` alongside ``nomic-embed-text-v1.5``,
  ``jina-embeddings-v2-base-code-q8_0`` alongside the base name)

``_clean_ollama_models`` filters the blobs and collapses the quant
variants so only real, de-duplicated model names remain.
"""
from __future__ import annotations

from app.routers.setup import _clean_ollama_models


def test_drops_sha256_blob_digests():
    raw = [
        "llama3.1-8b",
        "sha256-0ba8f0e314b4264dfd19df045cde9d4c394a52474bf92ed6a3de22a4ca31a177",
        "sha256-34bb5ab01051a11372a91f95f3fbbc51173eed8e7f13ec395b9ae9b8bd0e242b",
    ]
    assert _clean_ollama_models(raw) == ["llama3.1-8b"]


def test_collapses_gguf_quant_variants():
    """A ``.Q8_0`` / ``-q8_0`` quant variant is dropped when the base
    name is also present — no duplicate badges for one model."""
    raw = [
        "nomic-embed-text-v1.5.Q8_0",
        "nomic-embed-text-v1.5",
        "jina-embeddings-v2-base-code-q8_0",
        "jina-embeddings-v2-base-code",
        "bge-reranker-v2-m3.Q4_K_M",
        "bge-reranker-v2-m3",
    ]
    assert _clean_ollama_models(raw) == [
        "nomic-embed-text-v1.5",
        "jina-embeddings-v2-base-code",
        "bge-reranker-v2-m3",
    ]


def test_strips_quant_suffix_when_base_absent():
    """If only the quant-tagged form exists, strip the suffix so the
    real model name still shows (rather than the raw ``*.Q8_0`` blob name)."""
    assert _clean_ollama_models(["nomic-embed-text-v1.5.Q8_0"]) == [
        "nomic-embed-text-v1.5"
    ]


def test_preserves_order_and_real_models():
    raw = ["llama3.1-8b", "llama3.2-3b"]
    assert _clean_ollama_models(raw) == ["llama3.1-8b", "llama3.2-3b"]


def test_full_live_tag_list_reduces_to_real_models():
    """Mirrors the live Quenchforge /api/tags payload that exposed the bug:
    10 sha256 blobs + 3 quant duplicates collapse to 5 real models."""
    raw = [
        "bge-reranker-v2-m3.Q4_K_M",
        "bge-reranker-v2-m3",
        "jina-embeddings-v2-base-code-q8_0",
        "jina-embeddings-v2-base-code",
        "llama3.1-8b",
        "llama3.2-3b",
        "nomic-embed-text-v1.5.Q8_0",
        "nomic-embed-text-v1.5",
        "sha256-0ba8f0e314b4264dfd19df045cde9d4c394a52474bf92ed6a3de22a4ca31a177",
        "sha256-34bb5ab01051a11372a91f95f3fbbc51173eed8e7f13ec395b9ae9b8bd0e242b",
        "sha256-455f34728c9b5dd3376378bfb809ee166c145b0b4c1f1a6feca069055066ef9a",
    ]
    assert _clean_ollama_models(raw) == [
        "bge-reranker-v2-m3",
        "jina-embeddings-v2-base-code",
        "llama3.1-8b",
        "llama3.2-3b",
        "nomic-embed-text-v1.5",
    ]


def test_skips_empty_names():
    assert _clean_ollama_models(["", "llama3.1-8b", ""]) == ["llama3.1-8b"]
