# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""GET /providers/routing lists the chat models the local server serves.

Live stack, 2026-09-27: the endpoint answered ``ollama_available: true,
ollama_models: []`` while the server on :11434 served five models. It imported
the list by name beside the function that rebinds it.
"""
from __future__ import annotations

import asyncio

import pytest

from app.routers import providers
from core.routing import smart_router


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch):
    """A local server whose catalog is learned by the check, as in production:
    the list is empty until the check runs, and the check rebinds it."""
    monkeypatch.setattr(smart_router, "_ollama_models", [])

    def serve(names: list[str], *, available: bool = True):
        async def check() -> bool:
            smart_router._ollama_models = list(names)
            return available

        monkeypatch.setattr(smart_router, "_check_ollama", check)

    return serve


def test_it_lists_what_the_check_found(served) -> None:
    served(["gemma-4-26b-a4b", "qwen3.5-4b-instruct"])
    out = asyncio.run(providers.get_routing_info())
    assert out["ollama_available"] is True
    assert out["ollama_models"] == ["gemma-4-26b-a4b", "qwen3.5-4b-instruct"]


def test_it_leaves_out_what_cannot_chat(served) -> None:
    served(["nomic-embed-text-v1.5", "gemma-4-26b-a4b", "bge-reranker-v2-m3", "sha256-abc"])
    assert asyncio.run(providers.get_routing_info())["ollama_models"] == ["gemma-4-26b-a4b"]


def test_an_unavailable_server_lists_nothing(served) -> None:
    served(["gemma-4-26b-a4b"], available=False)
    out = asyncio.run(providers.get_routing_info())
    assert out["ollama_available"] is False
    assert out["ollama_models"] == []
