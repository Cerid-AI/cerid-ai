# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for RAG pipeline optimizations and resilience fixes.

Covers:
- Client singleton locking (llm_client, bifrost, internal_llm)
- Degradation tier logic (new chromadb breaker, tier transitions)
- ChromaDB circuit breaker registration and integration
- Parallel execution (orchestrator, BM25 timeout)
- Reranker warmup
- Technical query classifier upgrade
"""

from __future__ import annotations

import asyncio
import sys
import time
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Stub heavy reranker deps before import, but ONLY if they aren't already
# real modules (avoids polluting other tests that use the real packages).
class _UnionableMeta(type):
    def __or__(cls, other):
        import typing  # noqa: I001

        return typing.Union[cls, other]

    def __ror__(cls, other):
        import typing  # noqa: I001

        return typing.Union[other, cls]


class _Stub1(metaclass=_UnionableMeta):  # InferenceSession
    pass


class _Stub2(metaclass=_UnionableMeta):  # SessionOptions
    pass


class _Stub3(metaclass=_UnionableMeta):  # Tokenizer
    pass

for _mod_name in ("onnxruntime", "huggingface_hub", "tokenizers"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = ModuleType(_mod_name)

_ort = sys.modules["onnxruntime"]
if not hasattr(_ort, "__file__"):  # our stub, not real onnxruntime
    _ort.InferenceSession = _Stub1  # type: ignore[attr-defined]
    _ort.SessionOptions = _Stub2  # type: ignore[attr-defined]
_hf = sys.modules["huggingface_hub"]
if not hasattr(_hf, "__file__"):
    _hf.hf_hub_download = MagicMock(return_value="/fake")  # type: ignore[attr-defined]
_tok = sys.modules["tokenizers"]
if not hasattr(_tok, "__file__"):
    _tok.Tokenizer = _Stub3  # type: ignore[attr-defined]


# =========================================================================
# 1. TestClientSingletonLocking
# =========================================================================


class TestClientSingletonLocking:
    """Verify asyncio.Lock prevents duplicate client creation."""

    @pytest.mark.asyncio
    async def test_llm_client_concurrent_init(self):
        """10 concurrent _get_client() calls create exactly 1 httpx.AsyncClient."""
        import utils.llm_client as mod

        mod._client = None  # ensure clean state

        mock_client = MagicMock()
        mock_client.is_closed = False
        call_count = 0

        def fake_async_client(**kwargs):
            nonlocal call_count
            call_count += 1
            return mock_client

        with patch("utils.llm_client.httpx") as mock_httpx:
            mock_httpx.AsyncClient = fake_async_client
            mock_httpx.Timeout = MagicMock()
            mock_httpx.Limits = MagicMock()

            results = await asyncio.gather(
                *[mod._get_client() for _ in range(10)]
            )

        assert call_count == 1, f"Expected 1 client created, got {call_count}"
        assert all(r is mock_client for r in results)

    @pytest.mark.asyncio
    async def test_bifrost_client_concurrent_init(self):
        """10 concurrent get_bifrost_client() calls create exactly 1 client."""
        import utils.bifrost as mod

        mod._client = None

        mock_client = MagicMock()
        mock_client.is_closed = False
        call_count = 0

        def fake_async_client(**kwargs):
            nonlocal call_count
            call_count += 1
            return mock_client

        with patch("utils.bifrost.httpx") as mock_httpx:
            mock_httpx.AsyncClient = fake_async_client
            mock_httpx.Limits = MagicMock()

            results = await asyncio.gather(
                *[mod.get_bifrost_client() for _ in range(10)]
            )

        assert call_count == 1, f"Expected 1 client created, got {call_count}"
        assert all(r is mock_client for r in results)

    @pytest.mark.asyncio
    async def test_ollama_client_concurrent_init(self):
        """10 concurrent _get_ollama_client() calls create exactly 1 client."""
        import utils.internal_llm as mod

        mod._ollama_client = None

        mock_client = MagicMock()
        mock_client.is_closed = False
        call_count = 0

        def fake_async_client(**kwargs):
            nonlocal call_count
            call_count += 1
            return mock_client

        with patch("utils.internal_llm.httpx") as mock_httpx:
            mock_httpx.AsyncClient = fake_async_client
            mock_httpx.Timeout = MagicMock()
            mock_httpx.Limits = MagicMock()

            results = await asyncio.gather(
                *[mod._get_ollama_client() for _ in range(10)]
            )

        assert call_count == 1, f"Expected 1 client created, got {call_count}"
        assert all(r is mock_client for r in results)

    @pytest.mark.asyncio
    async def test_client_recreated_after_close(self):
        """A closed client is replaced on next _get_client() call."""
        import utils.llm_client as mod

        closed_client = MagicMock()
        closed_client.is_closed = True
        mod._client = closed_client

        new_client = MagicMock()
        new_client.is_closed = False

        with patch("utils.llm_client.httpx") as mock_httpx:
            mock_httpx.AsyncClient = MagicMock(return_value=new_client)
            mock_httpx.Timeout = MagicMock()
            mock_httpx.Limits = MagicMock()

            result = await mod._get_client()

        assert result is new_client
        assert result is not closed_client


# =========================================================================
# 2. TestDegradationTierLogic
# =========================================================================


class TestDegradationTierLogic:
    """Validate the multi-tier degradation state machine."""

    def _make_manager(self):
        from utils.degradation import DegradationManager
        return DegradationManager()

    @patch("utils.degradation._redis_down", return_value=True)
    @patch("utils.degradation._any_open", return_value=False)
    @patch("utils.degradation._all_open", return_value=False)
    def test_redis_down_degrades_to_direct(self, mock_all, mock_any, mock_redis):
        """Redis down alone -> DIRECT (skip caching, still retrieve)."""
        from utils.degradation import DegradationTier
        mgr = self._make_manager()
        assert mgr.current_tier() == DegradationTier.DIRECT

    @patch("utils.degradation._redis_down", return_value=True)
    def test_redis_down_plus_chromadb_down_is_offline(self, mock_redis):
        """Redis down + chromadb breaker open -> OFFLINE."""
        from utils.degradation import DegradationTier

        def fake_any_open(names):
            if "chromadb" in names:
                return True
            return False

        with patch("utils.degradation._any_open", side_effect=fake_any_open), \
             patch("utils.degradation._all_open", return_value=False):
            mgr = self._make_manager()
            assert mgr.current_tier() == DegradationTier.OFFLINE

    @patch("utils.degradation._redis_down", return_value=False)
    @patch("utils.degradation._any_open", return_value=False)
    @patch("utils.degradation._all_open", return_value=False)
    def test_all_healthy_is_full(self, mock_all, mock_any, mock_redis):
        """All services healthy -> FULL."""
        from utils.degradation import DegradationTier
        mgr = self._make_manager()
        assert mgr.current_tier() == DegradationTier.FULL

    @patch("utils.degradation._redis_down", return_value=False)
    @patch("utils.degradation._all_open", return_value=False)
    def test_reranker_down_is_lite(self, mock_all, mock_redis):
        """bifrost-rerank breaker open -> LITE."""
        from utils.degradation import DegradationTier

        def fake_any_open(names):
            if "bifrost-rerank" in names:
                return True
            return False

        with patch("utils.degradation._any_open", side_effect=fake_any_open):
            mgr = self._make_manager()
            assert mgr.current_tier() == DegradationTier.LITE

    @patch("utils.degradation._redis_down", return_value=False)
    @patch("utils.degradation._all_open", return_value=False)
    def test_chromadb_breaker_triggers_degradation(self, mock_all, mock_redis):
        """chromadb breaker open (in _CHROMADB_BREAKERS) -> LITE."""
        from utils.degradation import DegradationTier

        def fake_any_open(names):
            if "chromadb" in names:
                return True
            return False

        with patch("utils.degradation._any_open", side_effect=fake_any_open):
            mgr = self._make_manager()
            assert mgr.current_tier() == DegradationTier.LITE


# =========================================================================
# 3. TestChromaDBCircuitBreaker
# =========================================================================


class TestChromaDBCircuitBreaker:
    """Validate the chromadb breaker registration and integration."""

    def test_chromadb_breaker_registered(self):
        """get_breaker('chromadb') returns a valid AsyncCircuitBreaker."""
        from utils.circuit_breaker import AsyncCircuitBreaker, get_breaker
        breaker = get_breaker("chromadb")
        assert isinstance(breaker, AsyncCircuitBreaker)
        assert breaker.name == "chromadb"

    @pytest.mark.asyncio
    async def test_query_domain_raises_on_circuit_open(self):
        """When chromadb breaker is OPEN, CircuitOpenError propagates to caller."""
        from utils.circuit_breaker import CircuitOpenError, CircuitState, get_breaker

        breaker = get_breaker("chromadb")
        breaker._state = CircuitState.OPEN
        breaker._last_failure_time = time.monotonic()

        mock_collection = MagicMock()
        mock_chroma = MagicMock()
        col_stub = MagicMock()
        col_stub.name = "kb_general"
        mock_chroma.list_collections.return_value = [col_stub]
        mock_chroma.get_collection.return_value = mock_collection

        from agents.decomposer import multi_domain_query

        with patch("agents.decomposer.get_chroma", return_value=mock_chroma), \
             patch("agents.decomposer.DOMAINS", ["general"]), \
             patch("agents.decomposer.config") as mock_config:
            mock_config.collection_name = lambda d: f"kb_{d}"
            # CircuitOpenError propagates — the caller (query_agent) handles degradation
            with pytest.raises(CircuitOpenError):
                await multi_domain_query("test query", domains=["general"],
                                         chroma_client=mock_chroma)

        breaker.reset()

    @pytest.mark.asyncio
    async def test_chromadb_failures_trip_breaker(self):
        """5 consecutive failures trip the chromadb breaker to OPEN."""
        from utils.circuit_breaker import CircuitState, get_breaker

        breaker = get_breaker("chromadb")
        breaker.reset()

        async def failing_fn():
            raise RuntimeError("connection refused")

        for _ in range(5):
            with pytest.raises(RuntimeError):
                await breaker.call(failing_fn)

        assert breaker.state == CircuitState.OPEN
        breaker.reset()


# =========================================================================
# 4. TestParallelExecution
# =========================================================================


class TestParallelExecution:
    """Validate parallel KB + memory execution in the orchestrator."""

    @pytest.mark.asyncio
    async def test_orchestrator_runs_kb_and_memory_in_parallel(self):
        """KB and memory tasks run concurrently, not sequentially."""
        sleep_s = 0.1  # 100ms

        async def slow_agent_query(**kwargs):
            await asyncio.sleep(sleep_s)
            return {"results": [], "context": "", "strategy": "test"}

        async def slow_recall(**kwargs):
            await asyncio.sleep(sleep_s)
            return []

        # agent_query is imported lazily inside orchestrated_query, so
        # we patch at the source module where it's defined.
        with patch("agents.query_agent.agent_query",
                    new=AsyncMock(side_effect=slow_agent_query)), \
             patch("agents.memory.recall_memories",
                    new=AsyncMock(side_effect=slow_recall)):
            from agents.retrieval_orchestrator import orchestrated_query

            start = time.monotonic()
            result = await orchestrated_query(
                query="test query",
                rag_mode="smart",
            )
            elapsed = time.monotonic() - start

        # Parallel: ~100ms. Serial would be ~200ms. Allow generous margin.
        assert elapsed < 0.25, f"Parallel execution took {elapsed:.3f}s (expected <0.25s)"
        assert "source_breakdown" in result

    @pytest.mark.asyncio
    async def test_orchestrator_handles_memory_failure(self):
        """Memory recall failure doesn't break KB results."""
        async def ok_agent_query(**kwargs):
            return {"results": [{"content": "kb result"}], "context": "ctx", "strategy": "ok"}

        async def failing_recall(**kwargs):
            raise RuntimeError("memory exploded")

        with patch("agents.query_agent.agent_query",
                    new=AsyncMock(side_effect=ok_agent_query)), \
             patch("agents.memory.recall_memories",
                    new=AsyncMock(side_effect=failing_recall)):
            from agents.retrieval_orchestrator import orchestrated_query
            result = await orchestrated_query(query="test", rag_mode="smart")

        assert result["source_status"]["kb"] == "ok"
        assert result["source_breakdown"]["memory"] == []

    @pytest.mark.asyncio
    async def test_create_task_used_for_parallel(self):
        """Verify asyncio.create_task is used for parallel orchestration."""
        tasks_created = []
        real_create_task = asyncio.create_task

        def tracking_create_task(coro):
            task = real_create_task(coro)
            tasks_created.append(task)
            return task

        async def mock_query(**kwargs):
            return {"results": [], "context": "", "strategy": "test"}

        with patch("agents.query_agent.agent_query",
                    new=AsyncMock(side_effect=mock_query)), \
             patch("agents.memory.recall_memories",
                    new=AsyncMock(return_value=[])), \
             patch("asyncio.create_task", side_effect=tracking_create_task):
            from agents.retrieval_orchestrator import orchestrated_query
            await orchestrated_query(query="test", rag_mode="smart")

        # smart mode creates at least 2 tasks (kb + memory)
        assert len(tasks_created) >= 2, f"Expected >=2 tasks, got {len(tasks_created)}"

    @pytest.mark.asyncio
    async def test_bm25_timeout_returns_gracefully(self):
        """BM25 timeout (2s) doesn't block vector-only results."""
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "ids": [["c1"]],
            "documents": [["doc content"]],
            "metadatas": [[{"artifact_id": "a1", "filename": "test.txt"}]],
            "distances": [[0.2]],
        }

        mock_chroma = MagicMock()
        col_stub = MagicMock()
        col_stub.name = "kb_general"
        mock_chroma.list_collections.return_value = [col_stub]
        mock_chroma.get_collection.return_value = mock_collection

        from agents.decomposer import multi_domain_query
        from utils.circuit_breaker import get_breaker
        breaker = get_breaker("chromadb")
        breaker.reset()

        with patch("agents.decomposer.get_chroma", return_value=mock_chroma), \
             patch("agents.decomposer.DOMAINS", ["general"]), \
             patch("agents.decomposer.config") as mock_config:

            mock_config.collection_name = lambda d: f"kb_{d}"
            mock_config.HYBRID_VECTOR_WEIGHT = 0.7
            mock_config.HYBRID_KEYWORD_WEIGHT = 0.3

            start = time.monotonic()
            results = await multi_domain_query(
                "test query", domains=["general"], chroma_client=mock_chroma,
            )
            elapsed = time.monotonic() - start

        assert elapsed < 3.0, f"Query took {elapsed:.3f}s (expected <3s)"
        assert len(results) >= 1


# =========================================================================
# 5. TestRerankerWarmup
# =========================================================================


class TestRerankerWarmup:
    """Validate reranker model warmup."""

    def test_warmup_loads_model(self):
        """warmup() calls _load_model() when session is None."""
        import utils.reranker as reranker_mod
        reranker_mod._session = None
        with patch.object(reranker_mod, "_load_model") as mock_load:
            mock_load.return_value = (MagicMock(), MagicMock())
            reranker_mod.warmup()
            mock_load.assert_called_once()

    def test_warmup_failure_is_nonfatal(self):
        """warmup() swallows exceptions from _load_model()."""
        import utils.reranker as reranker_mod
        reranker_mod._session = None
        with patch.object(reranker_mod, "_load_model",
                          side_effect=RuntimeError("model download failed")):
            # Must not raise
            reranker_mod.warmup()


# =========================================================================
# 6. TestTechnicalQueryClassifier
# =========================================================================


class TestTechnicalQueryClassifier:
    """Validate technical term upgrade in retrieval gate."""

    def test_what_is_algorithm_upgraded_to_full(self):
        """'What is the algorithm' -> full (technical term upgrade)."""
        from utils.retrieval_gate import classify_retrieval_need
        decision = classify_retrieval_need("What is the algorithm")
        assert decision.action == "full"
        assert decision.reason == "technical_term_upgrade"

    def test_simple_greeting_stays_light(self):
        """'What is the weather?' -> light (no technical terms)."""
        from utils.retrieval_gate import classify_retrieval_need
        decision = classify_retrieval_need("What is the weather?")
        assert decision.action == "light"
        assert decision.reason == "simple_lookup"

    def test_define_protocol_upgraded(self):
        """'Define the HTTP protocol' -> full (technical term)."""
        from utils.retrieval_gate import classify_retrieval_need
        decision = classify_retrieval_need("Define the HTTP protocol")
        assert decision.action == "full"
        assert decision.reason == "technical_term_upgrade"

    def test_complex_query_unaffected(self):
        """Already-complex query stays full regardless of technical terms."""
        from utils.retrieval_gate import classify_retrieval_need
        decision = classify_retrieval_need(
            "How does quantum entanglement work in multi-qubit systems?"
        )
        assert decision.action == "full"
        # Reason should be complex_query, not technical_term_upgrade
        assert decision.reason == "complex_query"
