# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The /health llm lane must name a model the container can actually see.

F163: the quenchforge branch read QUENCHFORGE_DEFAULT_MODEL — the DAEMON's own
knob, which lives in its launchd plist and is never present in the cerid
container — so /health printed "unset" on every quenchforge deployment.
F007: that env var's only reader in the whole repo was this label, while
INTERNAL_LLM_MODEL is the pin chat.py, sdk.py, settings.py and provider_state
all read. Two knobs, one of them decorative.
"""

from __future__ import annotations

import core.utils.inference_health as ih
from core.utils.inference_routing import get_routing_snapshot


def _clear(monkeypatch) -> None:
    for var in (
        "INTERNAL_LLM_PROVIDER", "INTERNAL_LLM_MODEL", "EMBEDDINGS_PROVIDER",
        "RERANK_PROVIDER", "QUENCHFORGE_URL", "OLLAMA_URL",
        "QUENCHFORGE_DEFAULT_MODEL", "RETRIEVAL_SPARSE_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)
    ih.reset()


def test_quenchforge_llm_lane_reports_the_cerid_side_pin(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    monkeypatch.setenv("INTERNAL_LLM_MODEL", "llama3.1-8b")
    # The daemon-side knob is set on the daemon, not in this process.
    snap = get_routing_snapshot()
    assert snap["llm"]["model"] == "llama3.1-8b"


def test_quenchforge_llm_lane_does_not_read_the_daemon_env_var(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    monkeypatch.setenv("INTERNAL_LLM_MODEL", "llama3.1-8b")
    monkeypatch.setenv("QUENCHFORGE_DEFAULT_MODEL", "some-other-model")
    snap = get_routing_snapshot()
    assert snap["llm"]["model"] == "llama3.1-8b"


def test_llm_lane_surfaces_the_model_that_actually_served(monkeypatch):
    """The pin is intent; the daemon substitutes its loaded slot. Report both."""
    _clear(monkeypatch)
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    monkeypatch.setenv("INTERNAL_LLM_MODEL", "llama3.1-8b")
    ih.record_success(
        "llm", provider="quenchforge", model="qwen2.5-7b-instruct-q4_k_m.gguf",
    )
    snap = get_routing_snapshot()
    assert snap["llm"]["model"] == "llama3.1-8b"
    assert snap["llm"]["serving_model"] == "qwen2.5-7b-instruct-q4_k_m.gguf"
