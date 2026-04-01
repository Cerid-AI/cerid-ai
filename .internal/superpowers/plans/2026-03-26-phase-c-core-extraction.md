# Phase C: Core Orchestrator Extraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure `src/mcp/` into `core/` (orchestration logic) and `app/` (HTTP bindings, stores) with enforced import boundaries — without breaking any existing functionality.

**Architecture:** Contract inversion pattern — `core/` defines abstract ABCs, `app/` provides concrete implementations. Import direction enforced by `import-linter` in CI. Re-export bridges ensure zero breakage during migration.

**Tech Stack:** Python 3.11, FastAPI, import-linter, libcst (AST-aware import rewriting), pytest

**Spec:** `docs/superpowers/specs/2026-03-26-phase-c-core-extraction-design.md`

**Deferred:** `core/protocol/` (A2A/tool-registry layer) — spec mentions this aspirationally but no source files exist yet. Will be created in a future phase when A2A protocol matures. Do not attempt to move `utils/a2a_client.py` into `core/`.

---

## Task 1: Create Contract ABCs + Core Package Skeleton

**Files:**
- Create: `src/mcp/core/__init__.py`
- Create: `src/mcp/core/contracts/__init__.py`
- Create: `src/mcp/core/contracts/stores.py`
- Create: `src/mcp/core/contracts/llm.py`
- Create: `src/mcp/core/contracts/cache.py`
- Create: `src/mcp/core/contracts/embedding.py`
- Create: `src/mcp/core/contracts/audit.py`
- Test: `src/mcp/tests/test_contracts.py`

- [ ] **Step 1: Write contract import test**

```python
# tests/test_contracts.py
"""Verify all contract ABCs are importable and abstract."""
import pytest
from abc import ABC


def test_vector_store_is_abstract():
    from core.contracts.stores import VectorStore
    assert issubclass(VectorStore, ABC)
    with pytest.raises(TypeError):
        VectorStore()


def test_graph_store_is_abstract():
    from core.contracts.stores import GraphStore
    assert issubclass(GraphStore, ABC)
    with pytest.raises(TypeError):
        GraphStore()


def test_llm_client_is_abstract():
    from core.contracts.llm import LLMClient
    assert issubclass(LLMClient, ABC)
    with pytest.raises(TypeError):
        LLMClient()


def test_cache_store_is_abstract():
    from core.contracts.cache import CacheStore
    assert issubclass(CacheStore, ABC)
    with pytest.raises(TypeError):
        CacheStore()


def test_embedding_function_is_abstract():
    from core.contracts.embedding import EmbeddingFunction
    assert issubclass(EmbeddingFunction, ABC)
    with pytest.raises(TypeError):
        EmbeddingFunction()


def test_audit_log_is_abstract():
    from core.contracts.audit import AuditLog
    assert issubclass(AuditLog, ABC)
    with pytest.raises(TypeError):
        AuditLog()


def test_dataclasses_importable():
    from core.contracts.stores import SearchResult, ArtifactNode
    from core.contracts.llm import LLMResponse
    from core.contracts.audit import AuditEvent

    # Verify they're constructable
    sr = SearchResult(artifact_id="a1", chunk_id="c1", content="text", metadata={}, distance=0.5)
    assert sr.artifact_id == "a1"

    an = ArtifactNode(id="a1", filename="f.pdf", domain="general", sub_category="notes",
                      tags=["test"], summary="A doc", quality_score=0.8)
    assert an.domain == "general"

    lr = LLMResponse(content="hello", model="test-model")
    assert lr.usage is None

    ae = AuditEvent(action="query", actor="user1", resource="test")
    assert ae.timestamp is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/mcp && python -m pytest tests/test_contracts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core'`

- [ ] **Step 3: Create core package skeleton**

```python
# src/mcp/core/__init__.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
```

```python
# src/mcp/core/contracts/__init__.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract interfaces — abstract boundaries between core and app layers."""

from core.contracts.audit import AuditEvent, AuditLog
from core.contracts.cache import CacheStore
from core.contracts.embedding import EmbeddingFunction
from core.contracts.llm import LLMClient, LLMResponse
from core.contracts.stores import ArtifactNode, GraphStore, SearchResult, VectorStore

__all__ = [
    "ArtifactNode",
    "AuditEvent",
    "AuditLog",
    "CacheStore",
    "EmbeddingFunction",
    "GraphStore",
    "LLMClient",
    "LLMResponse",
    "SearchResult",
    "VectorStore",
]
```

- [ ] **Step 4: Create stores.py (VectorStore + GraphStore)**

```python
# src/mcp/core/contracts/stores.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Abstract store contracts — VectorStore and GraphStore."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class SearchResult:
    """A single vector search result."""

    artifact_id: str
    chunk_id: str
    content: str
    metadata: dict[str, Any]
    distance: float


@dataclass
class ArtifactNode:
    """Core artifact metadata from the knowledge graph."""

    id: str
    filename: str
    domain: str
    sub_category: str
    tags: list[str]
    summary: str
    quality_score: float


class VectorStore(ABC):
    """Abstract vector store — ChromaDB, Pinecone, Weaviate, etc."""

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 10,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def get_by_ids(self, ids: list[str]) -> list[SearchResult]: ...

    @abstractmethod
    async def count(self) -> int: ...


class GraphStore(ABC):
    """Abstract knowledge graph — Neo4j, ArangoDB, etc."""

    @abstractmethod
    async def get_artifact(self, artifact_id: str) -> ArtifactNode | None: ...

    @abstractmethod
    async def get_related(
        self,
        artifact_ids: list[str],
        *,
        depth: int = 1,
        limit: int = 20,
    ) -> list[ArtifactNode]: ...

    @abstractmethod
    async def list_artifacts(
        self,
        *,
        domain: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[ArtifactNode]: ...

    @abstractmethod
    async def update_artifact(
        self, artifact_id: str, updates: dict[str, Any]
    ) -> None: ...

    @abstractmethod
    async def list_domains(self) -> list[str]: ...
```

- [ ] **Step 5: Create llm.py (LLMClient)**

```python
# src/mcp/core/contracts/llm.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Abstract LLM client contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMResponse:
    """Normalized LLM response."""

    content: str
    model: str
    usage: dict[str, int] | None = None


class LLMClient(ABC):
    """Abstract LLM caller — OpenRouter, Ollama, Bifrost, etc."""

    @abstractmethod
    async def call(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2000,
        breaker_name: str = "default",
    ) -> LLMResponse: ...
```

- [ ] **Step 6: Create cache.py (CacheStore)**

```python
# src/mcp/core/contracts/cache.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Abstract cache store contract."""

from __future__ import annotations

from abc import ABC, abstractmethod


class CacheStore(ABC):
    """Abstract cache — Redis, memcached, in-memory, etc."""

    @abstractmethod
    async def get(self, key: str) -> str | None: ...

    @abstractmethod
    async def set(
        self, key: str, value: str, *, ttl_seconds: int = 300
    ) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def append(
        self, key: str, value: str, *, max_len: int = 1000
    ) -> None:
        """Append to an ordered list (Redis LPUSH + LTRIM pattern)."""
        ...

    @abstractmethod
    async def get_list(
        self, key: str, *, start: int = 0, end: int = -1
    ) -> list[str]:
        """Read from an ordered list (Redis LRANGE pattern)."""
        ...
```

- [ ] **Step 7: Create embedding.py (EmbeddingFunction)**

```python
# src/mcp/core/contracts/embedding.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Abstract embedding function contract."""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingFunction(ABC):
    """Abstract embedding — ONNX, OpenAI, Cohere, etc."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...
```

- [ ] **Step 8: Create audit.py (AuditLog)**

```python
# src/mcp/core/contracts/audit.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Abstract audit log contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class AuditEvent:
    """An auditable action."""

    action: str
    actor: str  # user_id or client_id
    resource: str  # artifact_id, query text, etc.
    detail: dict[str, Any] | None = None
    timestamp: str | None = None  # ISO 8601, auto-filled if None


class AuditLog(ABC):
    """Abstract audit log — Redis, Postgres, append-only file, etc."""

    @abstractmethod
    async def record(self, event: AuditEvent) -> None: ...

    @abstractmethod
    async def query(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]: ...
```

- [ ] **Step 9: Run test to verify it passes**

Run: `cd src/mcp && python -m pytest tests/test_contracts.py -v`
Expected: All 7 tests PASS

- [ ] **Step 10: Commit**

```bash
git add src/mcp/core/ src/mcp/tests/test_contracts.py
git commit -m "feat(phase-c): add contract ABCs — VectorStore, GraphStore, LLMClient, CacheStore, EmbeddingFunction, AuditLog"
```

---

## Task 2: Configure Import Linter + CI Gate

**Files:**
- Modify: `pyproject.toml` (add `[tool.importlinter]` section)
- Modify: `src/mcp/requirements-dev.txt` (add `import-linter`)
- Modify: `.github/workflows/ci.yml:11-20` (add import-linter to lint job)

- [ ] **Step 1: Add import-linter to dev requirements**

Append to `src/mcp/requirements-dev.txt`:
```
import-linter
```

- [ ] **Step 2: Add import-linter config to pyproject.toml**

Append to the end of `pyproject.toml`:
```toml

[tool.importlinter]
root_packages = ["core", "app", "config"]

[[tool.importlinter.contracts]]
name = "core must not import app"
type = "forbidden"
source_modules = ["core"]
forbidden_modules = ["app", "routers", "services", "middleware", "parsers", "sync", "models", "deps", "tools", "main", "scheduler", "db", "eval", "stores", "agents"]

[[tool.importlinter.contracts]]
name = "config must not import core or app"
type = "forbidden"
source_modules = ["config"]
forbidden_modules = ["core", "app", "routers", "services", "middleware", "parsers", "sync", "models", "deps", "tools", "main", "scheduler", "agents", "utils"]
```

- [ ] **Step 3: Run import-linter locally to verify it passes**

Run: `cd src/mcp && pip install import-linter && python -m importlinter`
Expected: All contracts PASS (core/ currently only has contracts with no app imports)

- [ ] **Step 4: Add import-linter step to CI lint job**

In `.github/workflows/ci.yml`, inside the `lint` job steps (after the `ruff check` step), add:

```yaml
      - run: pip install import-linter
      - name: Import boundary check
        run: cd src/mcp && python -m importlinter
```

- [ ] **Step 5: Run full test suite to verify no breakage**

Run: `cd src/mcp && python -m pytest tests/ -x --tb=short -q`
Expected: All 1411+ tests PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/mcp/requirements-dev.txt .github/workflows/ci.yml
git commit -m "feat(phase-c): configure import-linter with core/app boundary enforcement in CI"
```

---

## Task 3: Extract Core Utilities — Isolated Modules (Phase 2)

These 6 utility files depend only on stdlib/config — no middleware or app imports. Move each to `core/utils/`, leaving a re-export bridge at the old location.

**Files:**
- Create: `src/mcp/core/utils/__init__.py`
- Move: `src/mcp/utils/circuit_breaker.py` → `src/mcp/core/utils/circuit_breaker.py`
- Move: `src/mcp/utils/llm_parsing.py` → `src/mcp/core/utils/llm_parsing.py`
- Move: `src/mcp/utils/text.py` → `src/mcp/core/utils/text.py`
- Move: `src/mcp/utils/time.py` → `src/mcp/core/utils/time.py`
- Move: `src/mcp/utils/claim_cache.py` → `src/mcp/core/utils/claim_cache.py`
- Move: `src/mcp/utils/contextual.py` → `src/mcp/core/utils/contextual.py`
- Modify: Each old `src/mcp/utils/*.py` becomes a re-export bridge

**Pattern for each file** (repeat for all 6):

- [ ] **Step 1: Create core/utils/__init__.py**

```python
# src/mcp/core/utils/__init__.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
```

- [ ] **Step 2: Move circuit_breaker.py to core/utils/**

```bash
cd src/mcp
cp utils/circuit_breaker.py core/utils/circuit_breaker.py
```

- [ ] **Step 3: Replace old file with re-export bridge**

Replace the entire contents of `src/mcp/utils/circuit_breaker.py` with:
```python
# Re-export bridge — module moved to core/utils/circuit_breaker.py
from core.utils.circuit_breaker import *  # noqa: F401,F403
from core.utils.circuit_breaker import CircuitOpenError, CircuitState, get_breaker  # noqa: F401
```

- [ ] **Step 4: Run tests + import-linter**

Run: `cd src/mcp && python -m pytest tests/ -x --tb=short -q && python -m importlinter`
Expected: All tests PASS, import-linter PASS

- [ ] **Step 5: Repeat for llm_parsing.py**

Copy `utils/llm_parsing.py` → `core/utils/llm_parsing.py`. Replace old with:
```python
from core.utils.llm_parsing import *  # noqa: F401,F403
```

Run: `cd src/mcp && python -m pytest tests/ -x --tb=short -q`

- [ ] **Step 6: Repeat for text.py**

Copy `utils/text.py` → `core/utils/text.py`. Replace old with:
```python
from core.utils.text import *  # noqa: F401,F403
```

Run: `cd src/mcp && python -m pytest tests/ -x --tb=short -q`

- [ ] **Step 7: Repeat for time.py**

Copy `utils/time.py` → `core/utils/time.py`. Replace old with:
```python
from core.utils.time import *  # noqa: F401,F403
```

Run: `cd src/mcp && python -m pytest tests/ -x --tb=short -q`

- [ ] **Step 8: Repeat for claim_cache.py**

Copy `utils/claim_cache.py` → `core/utils/claim_cache.py`. Replace old with:
```python
from core.utils.claim_cache import *  # noqa: F401,F403
```

Run: `cd src/mcp && python -m pytest tests/ -x --tb=short -q`

- [ ] **Step 9: Repeat for contextual.py**

Copy `utils/contextual.py` → `core/utils/contextual.py`. Replace old with:
```python
from core.utils.contextual import *  # noqa: F401,F403
```

Run: `cd src/mcp && python -m pytest tests/ -x --tb=short -q`

- [ ] **Step 10: Run import-linter after all moves**

Run: `cd src/mcp && python -m importlinter`
Expected: All contracts PASS

- [ ] **Step 11: Commit**

```bash
git add src/mcp/core/utils/ src/mcp/utils/circuit_breaker.py src/mcp/utils/llm_parsing.py src/mcp/utils/text.py src/mcp/utils/time.py src/mcp/utils/claim_cache.py src/mcp/utils/contextual.py
git commit -m "refactor(phase-c): extract 6 isolated utilities to core/utils/ with re-export bridges"
```

---

## Task 4: Fix Cross-Layer Dependencies — Extract Tracing (Phase 3)

Three utils files import from `middleware.request_id`. The contextvars accessors must be extracted to `core/utils/tracing.py` before these files can move to core.

**Files:**
- Create: `src/mcp/core/utils/tracing.py`
- Modify: `src/mcp/middleware/request_id.py:19-46` (import from core.utils.tracing instead of defining locally)

- [ ] **Step 1: Write tracing test**

```python
# tests/test_tracing.py
"""Verify tracing contextvars accessors work from core."""
from core.utils.tracing import get_request_id, get_client_id, tracing_headers, request_id_var, client_id_var


def test_get_request_id_returns_default():
    # contextvars have no value outside of middleware context
    rid = get_request_id()
    assert isinstance(rid, str)


def test_get_client_id_returns_default():
    cid = get_client_id()
    assert isinstance(cid, str)


def test_tracing_headers_returns_dict():
    headers = tracing_headers()
    assert isinstance(headers, dict)
    assert "X-Request-ID" in headers


def test_set_and_get_request_id():
    token = request_id_var.set("test-123")
    try:
        assert get_request_id() == "test-123"
        headers = tracing_headers()
        assert headers["X-Request-ID"] == "test-123"
    finally:
        request_id_var.reset(token)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/mcp && python -m pytest tests/test_tracing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.utils.tracing'`

- [ ] **Step 3: Create core/utils/tracing.py**

Extract lines 19-46 from `middleware/request_id.py` into a new file:

```python
# src/mcp/core/utils/tracing.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Request tracing context — pure contextvars accessors with zero HTTP dependency.

These functions read values set by the middleware but have no FastAPI/Starlette
imports themselves, making them safe for use in core agents and utilities.
"""

from __future__ import annotations

import contextvars

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
client_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "client_id", default="unknown"
)


def get_request_id() -> str:
    """Get the current request ID from context."""
    return request_id_var.get()


def get_client_id() -> str:
    """Get the current client ID from context."""
    return client_id_var.get()


def tracing_headers() -> dict[str, str]:
    """Build tracing headers from current context for outgoing HTTP calls."""
    return {
        "X-Request-ID": request_id_var.get(),
        "X-Client-ID": client_id_var.get(),
    }
```

- [ ] **Step 4: Update middleware/request_id.py to import from core.utils.tracing**

Replace the contextvars declarations and accessor functions (lines 19-46) with imports:

```python
# In middleware/request_id.py, replace lines 19-46 with:
from core.utils.tracing import (  # noqa: F401
    client_id_var,
    get_client_id,
    get_request_id,
    request_id_var,
    tracing_headers,
)
```

Keep the `RequestIDMiddleware` class and Starlette imports intact — those are HTTP-dependent and stay in middleware.

- [ ] **Step 5: Run tests to verify**

Run: `cd src/mcp && python -m pytest tests/test_tracing.py tests/ -x --tb=short -q`
Expected: All tests PASS (existing middleware tests + new tracing tests)

- [ ] **Step 6: Run import-linter**

Run: `cd src/mcp && python -m importlinter`
Expected: PASS (core.utils.tracing has no app/middleware imports)

- [ ] **Step 7: Commit**

```bash
git add src/mcp/core/utils/tracing.py src/mcp/middleware/request_id.py src/mcp/tests/test_tracing.py
git commit -m "refactor(phase-c): extract tracing contextvars to core/utils/tracing.py — breaks utils→middleware coupling"
```

---

## Task 5: Move Cross-Layer Utils to Core (Phase 3 continued)

Now that tracing is in core, move the 5 utils that had middleware dependencies.

**Files:**
- Move: `utils/llm_client.py` → `core/utils/llm_client.py`
- Move: `utils/internal_llm.py` → `core/utils/internal_llm.py`
- Move: `utils/bifrost.py` → `core/utils/bifrost.py`
- Move: `utils/cache.py` → `core/utils/cache.py`
- Move: `utils/embeddings.py` → `core/utils/embeddings.py`

- [ ] **Step 1: Move llm_client.py — fix middleware import**

Copy `utils/llm_client.py` → `core/utils/llm_client.py`.

In the new `core/utils/llm_client.py`, change line 14:
```python
# OLD: from middleware.request_id import tracing_headers
# NEW:
from core.utils.tracing import tracing_headers
```

Also update `from utils.circuit_breaker import` → `from core.utils.circuit_breaker import` (line 15).

**Note:** `llm_client.py` has a lazy import at line 244: `from agents.hallucination.verification import CreditExhaustedError`. This is a core-utils→core-agents reference. Leave it as-is for now — the re-export bridge at `agents/hallucination/` will catch it. It will be updated to `from core.agents.hallucination.verification import CreditExhaustedError` when the hallucination subsystem moves in Task 8c Step 7.

Replace old `utils/llm_client.py` with re-export bridge:
```python
from core.utils.llm_client import *  # noqa: F401,F403
from core.utils.llm_client import call_llm, call_llm_raw  # noqa: F401
```

Run: `cd src/mcp && python -m pytest tests/ -x --tb=short -q && python -m importlinter`

- [ ] **Step 2: Move internal_llm.py**

Copy → `core/utils/internal_llm.py`. Update internal import:
```python
# OLD: from utils.circuit_breaker import ...
# NEW:
from core.utils.circuit_breaker import CircuitOpenError, get_breaker
```

Re-export bridge at old location. Run tests + import-linter.

- [ ] **Step 3: Move bifrost.py — fix middleware import**

Copy → `core/utils/bifrost.py`. Update:
```python
# OLD: from middleware.request_id import tracing_headers
# NEW:
from core.utils.tracing import tracing_headers

# OLD: from utils.circuit_breaker import get_breaker
# NEW:
from core.utils.circuit_breaker import get_breaker
```

Re-export bridge at old location. Run tests + import-linter.

- [ ] **Step 4: Move cache.py — fix middleware import**

Copy → `core/utils/cache.py`. Update:
```python
# OLD: from middleware.request_id import get_request_id
# NEW:
from core.utils.tracing import get_request_id

# OLD: from utils.time import utcnow_iso
# NEW:
from core.utils.time import utcnow_iso
```

Re-export bridge at old location. Run tests + import-linter.

- [ ] **Step 5: Move embeddings.py**

Copy → `core/utils/embeddings.py`. No middleware imports to fix (only imports `config`).

Re-export bridge at old location. Run tests + import-linter.

- [ ] **Step 6: Verify zero middleware imports in core**

Run: `grep -r "from middleware" src/mcp/core/`
Expected: 0 hits

- [ ] **Step 7: Commit**

```bash
git add src/mcp/core/utils/ src/mcp/utils/llm_client.py src/mcp/utils/internal_llm.py src/mcp/utils/bifrost.py src/mcp/utils/cache.py src/mcp/utils/embeddings.py
git commit -m "refactor(phase-c): extract 5 cross-layer utils to core/ — all middleware deps resolved via tracing.py"
```

---

## Task 6: Extract Retrieval Pipeline (Phase 4)

Move 7 RAG pipeline components. All depend only on `config/` and `utils/` (now in core).

**Files:**
- Create: `src/mcp/core/retrieval/__init__.py`
- Move: 7 files from `utils/` to `core/retrieval/`

- [ ] **Step 1: Create core/retrieval/ directory**

```python
# src/mcp/core/retrieval/__init__.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
```

- [ ] **Step 2: Move reranker.py**

Copy `utils/reranker.py` → `core/retrieval/reranker.py`. Re-export bridge at old location:
```python
from core.retrieval.reranker import *  # noqa: F401,F403
```

Run tests.

- [ ] **Step 3: Move retrieval_gate.py**

Copy → `core/retrieval/retrieval_gate.py`. Re-export bridge. Run tests.

- [ ] **Step 4: Move query_decomposer.py**

Copy → `core/retrieval/query_decomposer.py`. Re-export bridge. Run tests.

- [ ] **Step 5: Move context_assembler.py**

Copy → `core/retrieval/context_assembler.py`. Update internal import:
```python
# OLD: from utils.text import STOPWORDS, WORD_RE
# NEW:
from core.utils.text import STOPWORDS as _STOPWORDS
from core.utils.text import WORD_RE as _WORD_RE
```

Re-export bridge. Run tests.

- [ ] **Step 6: Move late_interaction.py**

Copy → `core/retrieval/late_interaction.py`. Re-export bridge. Run tests.

- [ ] **Step 7: Move semantic_cache.py**

Copy → `core/retrieval/semantic_cache.py`. Re-export bridge. Run tests.

- [ ] **Step 8: Move bm25.py**

Copy → `core/retrieval/bm25.py`. Re-export bridge. Run tests.

- [ ] **Step 9: Run import-linter + full test suite**

Run: `cd src/mcp && python -m pytest tests/ -x --tb=short -q && python -m importlinter`
Expected: All PASS

- [ ] **Step 10: Commit**

```bash
git add src/mcp/core/retrieval/ src/mcp/utils/reranker.py src/mcp/utils/retrieval_gate.py src/mcp/utils/query_decomposer.py src/mcp/utils/context_assembler.py src/mcp/utils/late_interaction.py src/mcp/utils/semantic_cache.py src/mcp/utils/bm25.py
git commit -m "refactor(phase-c): extract 7 retrieval pipeline components to core/retrieval/"
```

---

## Task 7: Extract Model Routing (Phase 5)

**Files:**
- Create: `src/mcp/core/routing/__init__.py`
- Move: `utils/smart_router.py` → `core/routing/smart_router.py`
- Move: `config/model_providers.py` → `core/routing/model_providers.py`

- [ ] **Step 1: Create core/routing/ and move smart_router.py**

```python
# src/mcp/core/routing/__init__.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
```

Copy `utils/smart_router.py` → `core/routing/smart_router.py`. Re-export bridge at old location.

- [ ] **Step 2: Move model_providers.py**

Copy `config/model_providers.py` → `core/routing/model_providers.py`. Re-export bridge at `config/model_providers.py`:
```python
from core.routing.model_providers import *  # noqa: F401,F403
```

- [ ] **Step 3: Run tests + import-linter**

Run: `cd src/mcp && python -m pytest tests/ -x --tb=short -q && python -m importlinter`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/mcp/core/routing/ src/mcp/utils/smart_router.py src/mcp/config/model_providers.py
git commit -m "refactor(phase-c): extract model routing to core/routing/"
```

---

## Task 8: Extract Agents to Core (Phase 6) — Refactor + Move

This is the highest-risk task. Agents must be refactored to remove `deps` and `db.*` imports before moving to core. Each agent is handled individually.

**IMPORTANT:** For each agent, first refactor in place (update signatures, remove forbidden imports), run tests, THEN move to `core/agents/`.

### Task 8a: Refactor + Move query_agent.py

**Files:**
- Modify: `src/mcp/agents/query_agent.py:19` (remove `from deps import get_chroma`)
- Modify: All callers — `routers/agents.py`, `tools.py`, `eval/harness.py`

- [ ] **Step 1: Identify all callers of agent_query()**

Run: `grep -rn "from agents.query_agent import\|agents.query_agent.agent_query\|from agents.query_agent" src/mcp/ --include="*.py" | grep -v __pycache__ | grep -v "core/"`

Document the full list — update each caller in later steps.

- [ ] **Step 2: Refactor agent_query() signature in place**

In `agents/query_agent.py`:
- Remove line 19: `from deps import get_chroma`
- The function already accepts `chroma_client`, `redis_client`, `neo4j_driver` as parameters (lines 834-847). Find all internal calls to `get_chroma()` and replace with the passed-in `chroma_client` parameter.
- Update internal `from utils.*` imports to `from core.utils.*` (circuit_breaker, llm_parsing, text, cache)
- **Critical — refactor 4 lazy `db.neo4j` imports** that the import-linter will flag:
  - Line 358: `from db.neo4j import find_related_artifacts` → Add `graph_store: GraphStore` parameter to the enclosing function, replace with `graph_store.get_related()`
  - Line 699: `from db.neo4j.artifacts import get_quality_scores` → Add `graph_store` parameter, replace with a batch call to `graph_store.get_artifact()` for quality scores
  - Line 727: `from db.neo4j.artifacts import get_artifact_summaries` → Same pattern, use `graph_store.get_artifact()` for summaries
  - Line 758: `from db.neo4j.artifacts import get_quality_and_summaries` → Same pattern, combine quality + summary from `graph_store.get_artifact()`
  - All callers (`routers/agents.py`, `tools.py`, `eval/harness.py`) must pass the `graph_store` instance alongside `neo4j_driver`

- [ ] **Step 3: Run tests to verify refactored agent still works**

Run: `cd src/mcp && python -m pytest tests/ -x --tb=short -q -k "query"`
Expected: All query-related tests PASS

- [ ] **Step 4: Move to core/agents/**

Create `src/mcp/core/agents/__init__.py`. Copy `agents/query_agent.py` → `core/agents/query_agent.py`. Add re-export bridge at old location.

- [ ] **Step 5: Run full tests + import-linter**

Run: `cd src/mcp && python -m pytest tests/ -x --tb=short -q && python -m importlinter`

- [ ] **Step 6: Commit**

```bash
git add src/mcp/core/agents/ src/mcp/agents/query_agent.py
git commit -m "refactor(phase-c): extract query_agent to core/ — remove deps.get_chroma coupling"
```

### Task 8b: Refactor + Move curator.py

- [ ] **Step 1: Refactor curator.py in place**

In `agents/curator.py`:
- Remove: `from db.neo4j.artifacts import list_artifacts, update_artifact_summary`
- Add parameter: `graph_store: GraphStore` to curation functions
- Update all calls to `list_artifacts()` → `graph_store.list_artifacts()`, `update_artifact_summary()` → `graph_store.update_artifact()`
- Update `from utils.*` → `from core.utils.*`

- [ ] **Step 2: Run tests, move to core/agents/, re-export bridge, tests again**

- [ ] **Step 3: Commit**

```bash
git commit -m "refactor(phase-c): extract curator to core/ — replace db.neo4j imports with GraphStore contract"
```

### Task 8c: Refactor + Move remaining agents

Repeat the pattern for each agent. The agents with simpler dependency profiles:

- [ ] **Step 1: Move self_rag.py** (depends only on `config` — simplest)

Copy → `core/agents/self_rag.py`. Re-export bridge. Tests.

- [ ] **Step 2: Move memory.py** (uses `utils.cache`, `utils.internal_llm`, `utils.llm_parsing`, `utils.time` — all now in core)

Update imports to `core.utils.*`. Copy → `core/agents/memory.py`. Re-export. Tests.

- [ ] **Step 3: Move rectify.py** (uses `utils.cache`, `utils.circuit_breaker`, `utils.llm_client`, `utils.time`)

Update imports. Copy → `core/agents/rectify.py`. Re-export. Tests.

- [ ] **Step 4: Move maintenance.py** (cross-imports `agents.rectify`)

Update import to `core.agents.rectify`. Copy → `core/agents/maintenance.py`. Re-export. Tests.

- [ ] **Step 5: Move audit.py** (uses `utils.cache` Redis constants — must use CacheStore or keep constants)

Refactor: keep `REDIS_CONV_METRICS_PREFIX` and `REDIS_VERIFICATION_METRICS_KEY` as constants in `core/utils/cache.py` (they're just string prefixes, not Redis operations). Update function signatures to accept `redis_client` parameter instead of importing from deps.

Copy → `core/agents/audit.py`. Re-export. Tests.

- [ ] **Step 6: Move trading_agent.py** (already injectable — takes driver as param)

Update imports. Copy → `core/agents/trading_agent.py`. Re-export. Tests.

- [ ] **Step 7: Move hallucination/ directory as a unit**

All 6 files (`__init__.py`, `extraction.py`, `patterns.py`, `persistence.py`, `streaming.py`, `verification.py`) move together. Rewrite these import categories:

**Internal cross-imports (18+ statements across 5 files):**
- `from agents.hallucination.extraction import ...` → `from core.agents.hallucination.extraction import ...`
- `from agents.hallucination.patterns import ...` → `from core.agents.hallucination.patterns import ...`
- `from agents.hallucination.persistence import ...` → `from core.agents.hallucination.persistence import ...`
- `from agents.hallucination.streaming import ...` → `from core.agents.hallucination.streaming import ...`
- `from agents.hallucination.verification import ...` → `from core.agents.hallucination.verification import ...`

**Utils imports (already in core):**
- `from utils.time import ...` → `from core.utils.time import ...`
- `from utils.internal_llm import ...` → `from core.utils.internal_llm import ...`
- `from utils.cache import ...` → `from core.utils.cache import ...`
- `from utils.circuit_breaker import ...` → `from core.utils.circuit_breaker import ...`
- `from utils.claim_cache import ...` → `from core.utils.claim_cache import ...`
- `from utils.llm_parsing import ...` → `from core.utils.llm_parsing import ...`
- `from utils.llm_client import ...` → `from core.utils.llm_client import ...`

**Cross-agent lazy imports:**
- `streaming.py:352` and `verification.py:1250`: `from agents.query_agent import lightweight_kb_query` → `from core.agents.query_agent import lightweight_kb_query`

**Also update `core/utils/llm_client.py`** (deferred from Task 5):
- Line 244: `from agents.hallucination.verification import CreditExhaustedError` → `from core.agents.hallucination.verification import CreditExhaustedError`

Copy entire `agents/hallucination/` → `core/agents/hallucination/`. Re-export bridge in old `agents/hallucination/__init__.py`:
```python
from core.agents.hallucination import *  # noqa: F401,F403
```

- [ ] **Step 8: Place app-layer agents**

Create `src/mcp/app/` directory structure (for now, just note the agents that STAY):
- `agents/triage.py` stays at current location (will move to `app/agents/` in Phase 8)
- `agents/trading_scheduler_jobs.py` stays at current location

- [ ] **Step 9: Verify zero forbidden imports in core/agents/**

Run: `grep -r "from deps\|from db\.\|from routers\|from middleware\|from services\|from parsers" src/mcp/core/agents/`
Expected: 0 hits

- [ ] **Step 10: Run full suite + import-linter**

Run: `cd src/mcp && python -m pytest tests/ -x --tb=short -q && python -m importlinter`

- [ ] **Step 11: Commit**

```bash
git add src/mcp/core/agents/ src/mcp/agents/
git commit -m "refactor(phase-c): extract all core agents — self_rag, memory, rectify, maintenance, audit, trading, hallucination/"
```

---

## Task 9: Create Concrete Store Implementations (Phase 7)

**Files:**
- Create: `src/mcp/stores/__init__.py`
- Create: `src/mcp/stores/chroma_store.py`
- Create: `src/mcp/stores/neo4j_store.py`
- Create: `src/mcp/stores/redis_cache.py`
- Create: `src/mcp/stores/redis_audit.py`
- Create: `src/mcp/stores/llm_clients.py`
- Test: `src/mcp/tests/test_stores.py`

These thin wrappers adapt existing DB code to the contract ABCs. They live at `src/mcp/stores/` now and will move to `app/stores/` in Phase 8.

- [ ] **Step 1: Write store contract compliance tests**

```python
# tests/test_stores.py
"""Verify concrete stores implement the contract ABCs."""
from core.contracts.stores import VectorStore, GraphStore
from core.contracts.cache import CacheStore
from core.contracts.audit import AuditLog
from core.contracts.llm import LLMClient


def test_chroma_store_implements_vector_store():
    from stores.chroma_store import ChromaVectorStore
    assert issubclass(ChromaVectorStore, VectorStore)


def test_neo4j_store_implements_graph_store():
    from stores.neo4j_store import Neo4jGraphStore
    assert issubclass(Neo4jGraphStore, GraphStore)


def test_redis_cache_implements_cache_store():
    from stores.redis_cache import RedisCacheStore
    assert issubclass(RedisCacheStore, CacheStore)


def test_redis_audit_implements_audit_log():
    from stores.redis_audit import RedisAuditLog
    assert issubclass(RedisAuditLog, AuditLog)


def test_llm_client_implements_contract():
    from stores.llm_clients import OpenRouterLLMClient
    assert issubclass(OpenRouterLLMClient, LLMClient)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/mcp && python -m pytest tests/test_stores.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stores'`

- [ ] **Step 3: Create store implementations**

Each store wraps existing operations. For example, `chroma_store.py` wraps the ChromaDB collection calls that `query_agent.py` currently does inline:

```python
# src/mcp/stores/__init__.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
```

```python
# src/mcp/stores/chroma_store.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""ChromaDB implementation of VectorStore contract."""

from __future__ import annotations

from typing import Any

from core.contracts.stores import SearchResult, VectorStore


class ChromaVectorStore(VectorStore):
    """VectorStore backed by a ChromaDB collection."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 10,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": top_k,
        }
        if where:
            kwargs["where"] = where
        if where_document:
            kwargs["where_document"] = where_document

        result = self._collection.query(**kwargs)

        results: list[SearchResult] = []
        if result and result.get("ids") and result["ids"][0]:
            ids = result["ids"][0]
            docs = result["documents"][0] if result.get("documents") else [""] * len(ids)
            metas = result["metadatas"][0] if result.get("metadatas") else [{}] * len(ids)
            dists = result["distances"][0] if result.get("distances") else [0.0] * len(ids)
            for i, chunk_id in enumerate(ids):
                meta = metas[i] or {}
                results.append(
                    SearchResult(
                        artifact_id=meta.get("artifact_id", ""),
                        chunk_id=chunk_id,
                        content=docs[i],
                        metadata=meta,
                        distance=dists[i],
                    )
                )
        return results

    async def get_by_ids(self, ids: list[str]) -> list[SearchResult]:
        result = self._collection.get(ids=ids, include=["documents", "metadatas"])
        results: list[SearchResult] = []
        if result and result.get("ids"):
            for i, chunk_id in enumerate(result["ids"]):
                meta = (result.get("metadatas") or [{}])[i] or {}
                doc = (result.get("documents") or [""])[i]
                results.append(
                    SearchResult(
                        artifact_id=meta.get("artifact_id", ""),
                        chunk_id=chunk_id,
                        content=doc,
                        metadata=meta,
                        distance=0.0,
                    )
                )
        return results

    async def count(self) -> int:
        return self._collection.count()
```

Create the remaining 4 store implementations:

```python
# src/mcp/stores/neo4j_store.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j implementation of GraphStore contract."""

from __future__ import annotations

from typing import Any

from core.contracts.stores import ArtifactNode, GraphStore


class Neo4jGraphStore(GraphStore):
    """GraphStore backed by Neo4j — wraps db/neo4j/ CRUD operations."""

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    async def get_artifact(self, artifact_id: str) -> ArtifactNode | None:
        from db.neo4j.artifacts import get_artifact
        raw = await get_artifact(self._driver, artifact_id)
        if not raw:
            return None
        return ArtifactNode(
            id=raw["artifact_id"], filename=raw.get("filename", ""),
            domain=raw.get("domain", ""), sub_category=raw.get("sub_category", ""),
            tags=raw.get("tags", []), summary=raw.get("summary", ""),
            quality_score=raw.get("quality_score", 0.0),
        )

    async def get_related(
        self, artifact_ids: list[str], *, depth: int = 1, limit: int = 20,
    ) -> list[ArtifactNode]:
        from db.neo4j import find_related_artifacts
        raw_list = await find_related_artifacts(self._driver, artifact_ids, depth=depth, limit=limit)
        return [
            ArtifactNode(
                id=r["artifact_id"], filename=r.get("filename", ""),
                domain=r.get("domain", ""), sub_category=r.get("sub_category", ""),
                tags=r.get("tags", []), summary=r.get("summary", ""),
                quality_score=r.get("quality_score", 0.0),
            )
            for r in raw_list
        ]

    async def list_artifacts(
        self, *, domain: str | None = None, offset: int = 0, limit: int = 100,
    ) -> list[ArtifactNode]:
        from db.neo4j.artifacts import list_artifacts
        raw_list = await list_artifacts(self._driver, domain=domain, offset=offset, limit=limit)
        return [
            ArtifactNode(
                id=r["artifact_id"], filename=r.get("filename", ""),
                domain=r.get("domain", ""), sub_category=r.get("sub_category", ""),
                tags=r.get("tags", []), summary=r.get("summary", ""),
                quality_score=r.get("quality_score", 0.0),
            )
            for r in raw_list
        ]

    async def update_artifact(self, artifact_id: str, updates: dict[str, Any]) -> None:
        from db.neo4j.artifacts import update_artifact_summary
        await update_artifact_summary(self._driver, artifact_id, updates)

    async def list_domains(self) -> list[str]:
        from db.neo4j.artifacts import list_domains
        return await list_domains(self._driver)
```

```python
# src/mcp/stores/redis_cache.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Redis implementation of CacheStore contract."""

from __future__ import annotations

from typing import Any

from core.contracts.cache import CacheStore


class RedisCacheStore(CacheStore):
    """CacheStore backed by Redis."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def get(self, key: str) -> str | None:
        val = self._redis.get(key)
        return val.decode() if isinstance(val, bytes) else val

    async def set(self, key: str, value: str, *, ttl_seconds: int = 300) -> None:
        self._redis.setex(key, ttl_seconds, value)

    async def delete(self, key: str) -> None:
        self._redis.delete(key)

    async def append(self, key: str, value: str, *, max_len: int = 1000) -> None:
        self._redis.lpush(key, value)
        self._redis.ltrim(key, 0, max_len - 1)

    async def get_list(self, key: str, *, start: int = 0, end: int = -1) -> list[str]:
        raw = self._redis.lrange(key, start, end)
        return [v.decode() if isinstance(v, bytes) else v for v in (raw or [])]
```

```python
# src/mcp/stores/redis_audit.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Redis implementation of AuditLog contract."""

from __future__ import annotations

import json
from typing import Any

from core.contracts.audit import AuditEvent, AuditLog
from core.utils.time import utcnow_iso


class RedisAuditLog(AuditLog):
    """AuditLog backed by Redis lists."""

    AUDIT_KEY = "cerid:audit_log"
    MAX_ENTRIES = 10000

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def record(self, event: AuditEvent) -> None:
        if event.timestamp is None:
            event.timestamp = utcnow_iso()
        entry = json.dumps({
            "action": event.action,
            "actor": event.actor,
            "resource": event.resource,
            "detail": event.detail,
            "timestamp": event.timestamp,
        })
        self._redis.lpush(self.AUDIT_KEY, entry)
        self._redis.ltrim(self.AUDIT_KEY, 0, self.MAX_ENTRIES - 1)

    async def query(
        self, *, actor: str | None = None, action: str | None = None,
        since: str | None = None, limit: int = 100,
    ) -> list[AuditEvent]:
        raw = self._redis.lrange(self.AUDIT_KEY, 0, limit * 3)  # over-fetch for filtering
        events: list[AuditEvent] = []
        for entry in raw or []:
            data = json.loads(entry.decode() if isinstance(entry, bytes) else entry)
            if actor and data.get("actor") != actor:
                continue
            if action and data.get("action") != action:
                continue
            if since and data.get("timestamp", "") < since:
                continue
            events.append(AuditEvent(
                action=data["action"], actor=data["actor"], resource=data["resource"],
                detail=data.get("detail"), timestamp=data.get("timestamp"),
            ))
            if len(events) >= limit:
                break
        return events
```

```python
# src/mcp/stores/llm_clients.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""OpenRouter/Bifrost implementation of LLMClient contract."""

from __future__ import annotations

from core.contracts.llm import LLMClient, LLMResponse


class OpenRouterLLMClient(LLMClient):
    """LLMClient that delegates to the existing core/utils/llm_client.call_llm()."""

    async def call(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2000,
        breaker_name: str = "default",
    ) -> LLMResponse:
        from core.utils.llm_client import call_llm
        result = await call_llm(
            messages=messages, model=model,
            temperature=temperature, max_tokens=max_tokens,
            breaker_name=breaker_name,
        )
        return LLMResponse(
            content=result.get("content", ""),
            model=result.get("model", model or "unknown"),
            usage=result.get("usage"),
        )
```

- [ ] **Step 4: Run store compliance tests**

Run: `cd src/mcp && python -m pytest tests/test_stores.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Add mock-based functional tests**

Add to `tests/test_stores.py` — these verify the contract implementations actually work with mock backends:

```python
import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.asyncio
async def test_chroma_store_search_returns_search_results():
    """ChromaVectorStore.search() converts ChromaDB response to SearchResult list."""
    from stores.chroma_store import ChromaVectorStore
    from core.contracts.stores import SearchResult

    mock_collection = MagicMock()
    mock_collection.query.return_value = {
        "ids": [["chunk-1", "chunk-2"]],
        "documents": [["doc 1 text", "doc 2 text"]],
        "metadatas": [[{"artifact_id": "a1"}, {"artifact_id": "a2"}]],
        "distances": [[0.1, 0.3]],
    }
    store = ChromaVectorStore(mock_collection)
    results = await store.search([0.1, 0.2, 0.3], top_k=2)
    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].artifact_id == "a1"
    assert results[0].distance == 0.1


@pytest.mark.asyncio
async def test_chroma_store_count():
    from stores.chroma_store import ChromaVectorStore

    mock_collection = MagicMock()
    mock_collection.count.return_value = 42
    store = ChromaVectorStore(mock_collection)
    assert await store.count() == 42


@pytest.mark.asyncio
async def test_redis_cache_get_set_delete():
    from stores.redis_cache import RedisCacheStore

    mock_redis = MagicMock()
    mock_redis.get.return_value = b"cached-value"
    store = RedisCacheStore(mock_redis)

    val = await store.get("key")
    assert val == "cached-value"
    mock_redis.get.assert_called_with("key")

    await store.set("key", "value", ttl_seconds=60)
    mock_redis.setex.assert_called_with("key", 60, "value")

    await store.delete("key")
    mock_redis.delete.assert_called_with("key")


@pytest.mark.asyncio
async def test_redis_audit_record_and_query():
    import json
    from stores.redis_audit import RedisAuditLog
    from core.contracts.audit import AuditEvent

    mock_redis = MagicMock()
    store = RedisAuditLog(mock_redis)

    event = AuditEvent(action="query", actor="user1", resource="kb")
    await store.record(event)
    mock_redis.lpush.assert_called_once()

    # Verify the stored JSON is valid
    stored = mock_redis.lpush.call_args[0][1]
    data = json.loads(stored)
    assert data["action"] == "query"
    assert data["actor"] == "user1"


@pytest.mark.asyncio
async def test_openrouter_llm_client_delegates():
    from stores.llm_clients import OpenRouterLLMClient
    from core.contracts.llm import LLMResponse

    with patch("core.utils.llm_client.call_llm") as mock_call:
        mock_call.return_value = {"content": "hello", "model": "test", "usage": {"tokens": 10}}
        client = OpenRouterLLMClient()
        resp = await client.call([{"role": "user", "content": "hi"}])
        assert isinstance(resp, LLMResponse)
        assert resp.content == "hello"
```

- [ ] **Step 6: Run all store tests**

Run: `cd src/mcp && python -m pytest tests/test_stores.py -v`
Expected: All 10 tests PASS (5 compliance + 5 functional)

- [ ] **Step 7: Run full suite**

Run: `cd src/mcp && python -m pytest tests/ -x --tb=short -q && python -m importlinter`

- [ ] **Step 8: Commit**

```bash
git add src/mcp/stores/ src/mcp/tests/test_stores.py
git commit -m "feat(phase-c): add concrete store implementations — ChromaVectorStore, Neo4jGraphStore, RedisCacheStore, RedisAuditLog, OpenRouterLLMClient"
```

---

## Task 10: Move Application Layer Into app/ (Phase 8)

**This is the highest-risk task.** All remaining top-level modules move under `app/`. Use `libcst` for AST-aware import rewriting.

**Files:**
- Create: `src/mcp/app/__init__.py` and all subdirectories
- Move: `main.py`, `deps.py`, `tools.py`, `scheduler.py` → `app/`
- Move: `routers/`, `middleware/`, `services/`, `parsers/`, `db/`, `sync/`, `models/`, `stores/`, `eval/` → `app/`
- Move: `agents/triage.py`, `agents/trading_scheduler_jobs.py` → `app/agents/`
- Modify: `src/mcp/Dockerfile:45` (CMD path change)
- Modify: `pyproject.toml` (ruff src, mypy files paths)
- Modify: `.github/workflows/ci.yml` (mypy working directory)

- [ ] **Step 1: Install libcst**

Run: `pip install libcst`

- [ ] **Step 2: Write the import rewrite script**

Create a one-time migration script `scripts/rewrite_imports.py` that uses `libcst` to find and rewrite all imports of moved modules.

```python
#!/usr/bin/env python3
# scripts/rewrite_imports.py — One-time libcst import rewriter for Phase C
"""Rewrites imports from old top-level modules to app.* prefixed paths.

Usage: cd src/mcp && python scripts/rewrite_imports.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import libcst as cst
import libcst.matchers as m

# Modules that moved under app/
MOVED_MODULES = {
    "routers", "middleware", "services", "parsers", "db", "sync",
    "models", "stores", "eval", "deps", "tools", "main", "scheduler",
}

# Directories to skip (core/ must not be rewritten)
SKIP_DIRS = {"core", "__pycache__", ".git", "node_modules"}


class ImportRewriter(cst.CSTTransformer):
    """Rewrites `from X.Y import Z` → `from app.X.Y import Z` for moved modules."""

    def __init__(self) -> None:
        self.changes: list[str] = []

    def _should_rewrite(self, module_parts: list[str]) -> bool:
        return bool(module_parts) and module_parts[0] in MOVED_MODULES

    def leave_ImportFrom(
        self, original: cst.ImportFrom, updated: cst.ImportFrom
    ) -> cst.ImportFrom:
        if updated.module is None:
            return updated
        # Get dotted name parts
        parts = []
        node = updated.module
        while isinstance(node, cst.Attribute):
            parts.insert(0, node.attr.value)
            node = node.value
        if isinstance(node, cst.Name):
            parts.insert(0, node.value)

        if not self._should_rewrite(parts):
            return updated

        # Prepend "app."
        new_parts = ["app"] + parts
        # Rebuild the module attribute chain
        new_module: cst.BaseExpression = cst.Name(new_parts[0])
        for part in new_parts[1:]:
            new_module = cst.Attribute(value=new_module, attr=cst.Name(part))

        self.changes.append(f"  from {'.'.join(parts)} → from {'.'.join(new_parts)}")
        return updated.with_changes(module=new_module)

    def leave_Import(
        self, original: cst.Import, updated: cst.Import
    ) -> cst.Import:
        if not isinstance(updated.names, (list, tuple)):
            return updated
        new_names = []
        changed = False
        for alias in updated.names:
            if not isinstance(alias, cst.ImportAlias):
                new_names.append(alias)
                continue
            parts = []
            node = alias.name
            while isinstance(node, cst.Attribute):
                parts.insert(0, node.attr.value)
                node = node.value
            if isinstance(node, cst.Name):
                parts.insert(0, node.value)

            if self._should_rewrite(parts):
                new_parts = ["app"] + parts
                new_name: cst.BaseExpression = cst.Name(new_parts[0])
                for part in new_parts[1:]:
                    new_name = cst.Attribute(value=new_name, attr=cst.Name(part))
                new_names.append(alias.with_changes(name=new_name))
                self.changes.append(f"  import {'.'.join(parts)} → import {'.'.join(new_parts)}")
                changed = True
            else:
                new_names.append(alias)
        return updated.with_changes(names=new_names) if changed else updated


def rewrite_file(path: Path, dry_run: bool = False) -> list[str]:
    source = path.read_text()
    tree = cst.parse_module(source)
    rewriter = ImportRewriter()
    new_tree = tree.visit(rewriter)
    if rewriter.changes and not dry_run:
        path.write_text(new_tree.code)
    return rewriter.changes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(".")
    total_changes = 0
    for py_file in sorted(root.rglob("*.py")):
        # Skip core/ and other excluded dirs
        if any(part in SKIP_DIRS for part in py_file.parts):
            continue
        changes = rewrite_file(py_file, dry_run=args.dry_run)
        if changes:
            prefix = "[DRY RUN] " if args.dry_run else ""
            print(f"{prefix}{py_file}:")
            for c in changes:
                print(c)
            total_changes += len(changes)

    print(f"\n{'Would rewrite' if args.dry_run else 'Rewrote'} {total_changes} imports.")
    if args.dry_run and total_changes:
        print("Run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
```

The script handles:
- `from routers.X import Y` → `from app.routers.X import Y`
- `from middleware.X import Y` → `from app.middleware.X import Y`
- `from services.X import Y` → `from app.services.X import Y`
- `from parsers.X import Y` → `from app.parsers.X import Y`
- `from db.X import Y` → `from app.db.X import Y`
- `from sync.X import Y` → `from app.sync.X import Y`
- `from models.X import Y` → `from app.models.X import Y`
- `from stores.X import Y` → `from app.stores.X import Y`
- `from deps import Y` → `from app.deps import Y`
- `from tools import Y` → `from app.tools import Y`
- `import main` → `import app.main`
- `from eval.X import Y` → `from app.eval.X import Y`

The script must NOT rewrite imports inside `core/` (those already point to `core.*`). Always run `--dry-run` first to review changes.

- [ ] **Step 3: Create app/ directory structure**

```bash
cd src/mcp
mkdir -p app/agents app/routers app/middleware app/services app/parsers app/db app/sync app/models app/stores app/eval
```

- [ ] **Step 4: Move all application modules**

```bash
cd src/mcp
# Top-level hub modules
mv main.py app/main.py
mv deps.py app/deps.py
mv tools.py app/tools.py
mv scheduler.py app/scheduler.py

# Directories
mv routers/ app/routers/
mv middleware/ app/middleware/
mv services/ app/services/
mv parsers/ app/parsers/
mv db/ app/db/
mv sync/ app/sync/
mv models/ app/models/
mv stores/ app/stores/
mv eval/ app/eval/

# App-layer agents
mv agents/triage.py app/agents/triage.py
mv agents/trading_scheduler_jobs.py app/agents/trading_scheduler_jobs.py
```

Create `app/__init__.py`:
```python
# src/mcp/app/__init__.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
```

- [ ] **Step 5: Run the libcst import rewriter**

Run: `cd src/mcp && python scripts/rewrite_imports.py`

This rewrites all imports in `app/` files. `core/` files are not touched (they already use `core.*` imports).

- [ ] **Step 6: Add re-export bridges at all old locations**

Create bridge files at every old module path. These are the safety net — any import the libcst script missed will be caught by bridges. Each bridge is a single `__init__.py` (or module file for top-level modules) that re-exports from the new `app/` location:

```python
# src/mcp/routers/__init__.py
"""Bridge — re-exports from app.routers for backward compatibility."""
from app.routers import *  # noqa: F401,F403

# src/mcp/middleware/__init__.py
"""Bridge — re-exports from app.middleware for backward compatibility."""
from app.middleware import *  # noqa: F401,F403

# src/mcp/services/__init__.py
"""Bridge — re-exports from app.services for backward compatibility."""
from app.services import *  # noqa: F401,F403

# src/mcp/parsers/__init__.py
"""Bridge — re-exports from app.parsers for backward compatibility."""
from app.parsers import *  # noqa: F401,F403

# src/mcp/db/__init__.py
"""Bridge — re-exports from app.db for backward compatibility."""
from app.db import *  # noqa: F401,F403

# src/mcp/sync/__init__.py
"""Bridge — re-exports from app.sync for backward compatibility."""
from app.sync import *  # noqa: F401,F403

# src/mcp/models/__init__.py
"""Bridge — re-exports from app.models for backward compatibility."""
from app.models import *  # noqa: F401,F403

# src/mcp/stores/__init__.py
"""Bridge — re-exports from app.stores for backward compatibility."""
from app.stores import *  # noqa: F401,F403

# src/mcp/eval/__init__.py
"""Bridge — re-exports from app.eval for backward compatibility."""
from app.eval import *  # noqa: F401,F403

# src/mcp/deps.py
"""Bridge — re-exports from app.deps for backward compatibility."""
from app.deps import *  # noqa: F401,F403

# src/mcp/tools.py
"""Bridge — re-exports from app.tools for backward compatibility."""
from app.tools import *  # noqa: F401,F403

# src/mcp/main.py
"""Bridge — re-exports from app.main for backward compatibility."""
from app.main import *  # noqa: F401,F403

# src/mcp/scheduler.py
"""Bridge — re-exports from app.scheduler for backward compatibility."""
from app.scheduler import *  # noqa: F401,F403
```

Total: 13 bridge modules (9 packages + 4 top-level modules). After verifying all tests pass with bridges in place, these can be gradually removed in follow-up PRs as external references are updated.

- [ ] **Step 7: Update Dockerfile CMD**

In `src/mcp/Dockerfile`, change line 45:
```dockerfile
# OLD: CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]
# NEW:
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8888"]
```

- [ ] **Step 8: Update pyproject.toml paths**

Update `[tool.ruff]` src, `[tool.mypy]` files, and `[tool.pytest.ini_options]` testpaths if needed. The `mypy_path` already points to `src/mcp` which contains both `core/` and `app/`.

- [ ] **Step 9: Update CI workflow paths**

In `.github/workflows/ci.yml`:
- `ruff check src/mcp/` → no change needed (still covers all subdirs)
- mypy: `cd src/mcp && python -m mypy .` → no change needed (`.` covers core/ and app/)
- Ensure the import-linter step still works (it reads `pyproject.toml`)

- [ ] **Step 10: Run all quality gates**

Run sequentially:
```bash
cd src/mcp
python -m ruff check .
python -m mypy . --config-file ../../pyproject.toml
python -m importlinter
python -m pytest tests/ -x --tb=short -q
```

ALL must pass. If any fail, fix before committing.

- [ ] **Step 11: Verify Docker build**

Run: `cd . && docker build -t cerid-mcp-test src/mcp/`
Expected: Build succeeds

- [ ] **Step 12: Commit (single atomic commit for entire Phase 8)**

```bash
git add -A src/mcp/
git commit -m "refactor(phase-c): move application layer into app/ — complete core/app separation with libcst import rewriting"
```

---

## Task 11: Licensing + Headers (Phase 9)

**Files:**
- Create: `src/mcp/core/LICENSE`
- Modify: `README.md` (add licensing table)

- [ ] **Step 1: Verify SPDX headers exist in all core/ files**

Run: `grep -rL "SPDX-License-Identifier" src/mcp/core/ --include="*.py" | grep -v __pycache__`
Expected: Only `__init__.py` files may be missing headers. Add them.

- [ ] **Step 2: Copy Apache-2.0 license to core/**

```bash
cp LICENSE src/mcp/core/LICENSE
```

- [ ] **Step 3: Add licensing table to README.md**

Add to the appropriate section in `README.md`:

```markdown
## Licensing

| Directory | License | Description |
|-----------|---------|-------------|
| `core/` | [Apache-2.0](src/mcp/core/LICENSE) | Orchestration engine, agents, retrieval, verification |
| `app/` | [Apache-2.0](LICENSE) | Application layer, routers, parsers, GUI |
| `plugins/` | [BSL-1.1](plugins/LICENSE) | Pro-tier extensions (converts to Apache-2.0 after 3 years) |
```

- [ ] **Step 4: Commit**

```bash
git add src/mcp/core/LICENSE README.md
git commit -m "docs(phase-c): add Apache-2.0 license to core/ and licensing table to README"
```

---

## Task 12: Final Verification

- [ ] **Step 1: Run complete CI-equivalent check locally**

```bash
cd .
# Lint
cd src/mcp && python -m ruff check .
# Typecheck
python -m mypy . --config-file ../../pyproject.toml
# Import boundaries
python -m importlinter
# Tests
python -m pytest tests/ -v --tb=short
# Docker
cd ../.. && docker build -t cerid-mcp-test src/mcp/
docker build -t cerid-web-test src/web/
```

- [ ] **Step 2: Verify success criteria from spec**

Run each check from spec section 10:
1. `python -m importlinter` — PASS
2. `python -m pytest tests/ -q` — 1411+ tests PASS
3. `cd src/web && npx vitest run` — 545+ tests PASS
4. Docker build + health check — PASS
5. `grep -r "from deps\|from db\.\|from routers\|from middleware\|from services\|from parsers" src/mcp/core/` — 0 hits
6. All 6 contracts exist in `core/contracts/`
7. No enterprise concepts in committed code

- [ ] **Step 3: Update tasks/todo.md**

Mark Phase C as complete with date and summary of changes.

- [ ] **Step 4: Final commit if any cleanup needed**

```bash
git commit -m "chore(phase-c): final verification — all gates pass"
```
