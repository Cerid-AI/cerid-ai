# cerid-trading-agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a production-grade, isolated DeFi/prediction-market trading agent that generates income via non-speed meta-strategies (liquidity provision, herd-fading, niche arb) while reusing Cerid AI's intelligence layer.

**Architecture:** LangGraph state machine orchestrating 5 Grok agents (Sentinel→Oracle→Risk Overseer→Ghost→Reflection) with conditional edges. Cerid-AI integration via HTTP calls to MCP server. Docker sandbox with wallet isolation.

**Tech Stack:** Python 3.11, LangGraph, PyTorch (M1 MPS), Hyperliquid SDK, py-clob-client, Streamlit, Redis, xAI API (Grok 4.20), Claude Max via Bifrost

---

## Phase 1: Scaffold & Docker (Days 1–3)

### Task 1: Repository Init

**Files:**
- Create: `pyproject.toml`, `Makefile`, `.env.example`, `.gitignore`, `src/__init__.py`, `src/config/__init__.py`, `src/utils/__init__.py`, `src/agents/__init__.py`, `src/exchanges/__init__.py`, `src/graph/__init__.py`, `tests/__init__.py`, `tests/conftest.py`

**Step 1: Write failing test**

```python
# tests/test_scaffold.py
"""Verify project scaffold is importable."""


def test_src_package_importable():
    import src
    assert hasattr(src, "__version__")


def test_subpackages_importable():
    import src.config
    import src.utils
    import src.agents
    import src.exchanges
    import src.graph
```

**Step 2: Verify failure**

```bash
cd ~/Develop/cerid-trading-agent && python -m pytest tests/test_scaffold.py -v
```

**Step 3: Write minimal implementation**

```toml
# pyproject.toml
[project]
name = "cerid-trading-agent"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27,<1",
    "langgraph>=0.4,<1",
    "langchain-openai>=0.3,<1",
    "hyperliquid-python-sdk>=0.9,<1",
    "py-clob-client>=0.18,<1",
    "pydantic>=2,<3",
    "pydantic-settings>=2,<3",
    "python-dotenv>=1,<2",
    "structlog>=24,<26",
    "websockets>=14,<16",
]

[project.optional-dependencies]
dev = [
    "pytest>=8,<9",
    "pytest-asyncio>=0.24,<1",
    "pytest-cov>=6,<7",
    "respx>=0.22,<1",
    "ruff>=0.9,<1",
    "mypy>=1.14,<2",
]

[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.backends._legacy:_Backend"

[tool.setuptools.packages.find]
include = ["src*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
pythonpath = ["src"]

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "SIM", "TCH"]

[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
disallow_untyped_defs = true
```

```makefile
# Makefile
.PHONY: install test lint typecheck fmt clean

install:
	pip install -e ".[dev]"

test:
	python -m pytest tests/ -v --tb=short

lint:
	ruff check src/ tests/

typecheck:
	mypy src/

fmt:
	ruff format src/ tests/
	ruff check --fix src/ tests/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist *.egg-info
```

```bash
# .env.example
XAI_API_KEY=xai-your-key-here
XAI_MODEL=grok-4.20-multi-agent-beta-0309
CERID_MCP_URL=http://localhost:8888
HYPERLIQUID_WALLET_ADDRESS=0x0000000000000000000000000000000000000000
HYPERLIQUID_PRIVATE_KEY=0x0000000000000000000000000000000000000000000000000000000000000000
HYPERLIQUID_TESTNET=true
POLYMARKET_API_KEY=your-key-here
POLYMARKET_API_SECRET=your-secret-here
POLYMARKET_PASSPHRASE=your-passphrase-here
COINGECKO_API_KEY=your-key-here
TRADING_MODE=paper
MAX_POSITION_USD=500
MAX_DAILY_LOSS_USD=200
LOG_LEVEL=INFO
```

```python
# src/__init__.py
"""cerid-trading-agent: Autonomous trading agent powered by Cerid AI."""
__version__ = "0.1.0"
```

```python
# tests/conftest.py
"""Shared pytest fixtures."""
from __future__ import annotations
import os

os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("XAI_API_KEY", "test-key")
os.environ.setdefault("CERID_MCP_URL", "http://localhost:8888")
os.environ.setdefault("HYPERLIQUID_WALLET_ADDRESS", "0x" + "0" * 40)
os.environ.setdefault("HYPERLIQUID_PRIVATE_KEY", "0x" + "0" * 64)
os.environ.setdefault("POLYMARKET_API_KEY", "test-key")
os.environ.setdefault("POLYMARKET_API_SECRET", "test-secret")
os.environ.setdefault("POLYMARKET_PASSPHRASE", "test-pass")
```

**Step 4: Verify pass**

```bash
cd ~/Develop/cerid-trading-agent && pip install -e ".[dev]" && python -m pytest tests/test_scaffold.py -v
```

**Step 5: Commit**

```bash
cd ~/Develop/cerid-trading-agent && git init && git add -A && git commit -m "task-1: repository scaffold with pyproject.toml, Makefile, directory structure"
```

---

### Task 2: Config Module

**Files:**
- Create: `src/config/settings.py`
- Create: `tests/test_config.py`

**Step 1: Write failing test**

```python
# tests/test_config.py
"""Tests for configuration module."""
from __future__ import annotations
import os


def test_settings_loads_defaults():
    from config.settings import Settings
    s = Settings()
    assert s.trading_mode == "paper"
    assert s.max_position_usd == 500
    assert s.max_daily_loss_usd == 200


def test_settings_xai_model_default():
    from config.settings import Settings
    s = Settings()
    assert s.xai_model == "grok-4.20-multi-agent-beta-0309"


def test_settings_cerid_mcp_url():
    from config.settings import Settings
    s = Settings()
    assert s.cerid_mcp_url == "http://localhost:8888"


def test_settings_rejects_live_without_explicit_flag():
    import pytest
    from pydantic import ValidationError
    os.environ["TRADING_MODE"] = "live"
    os.environ["LIVE_TRADING_CONFIRMED"] = "false"
    try:
        from config.settings import Settings
        with pytest.raises(ValidationError):
            Settings()
    finally:
        os.environ["TRADING_MODE"] = "paper"
        os.environ.pop("LIVE_TRADING_CONFIRMED", None)


def test_sentinel_thresholds():
    from config.settings import Settings
    s = Settings()
    assert s.liquidation_threshold_usd == 25_000
    assert s.sentinel_scan_interval_s == 30


def test_circuit_breaker_defaults():
    from config.settings import Settings
    s = Settings()
    assert s.circuit_breaker_failure_threshold == 3
    assert s.circuit_breaker_recovery_timeout_s == 60.0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`

**Step 3: Write minimal implementation**

```python
# src/config/settings.py
"""Application settings loaded from environment variables."""
from __future__ import annotations
from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # API keys
    xai_api_key: str = "test-key"
    xai_model: str = "grok-4.20-multi-agent-beta-0309"
    cerid_mcp_url: str = "http://localhost:8888"
    coingecko_api_key: str = ""

    # Hyperliquid
    hyperliquid_wallet_address: str = "0x" + "0" * 40
    hyperliquid_private_key: str = "0x" + "0" * 64
    hyperliquid_testnet: bool = True

    # Polymarket
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_passphrase: str = ""

    # Trading mode
    trading_mode: str = "paper"
    live_trading_confirmed: bool = False

    # Risk limits
    max_position_usd: int = 500
    max_daily_loss_usd: int = 200

    # Sentinel thresholds
    liquidation_threshold_usd: int = 25_000
    sentinel_scan_interval_s: int = 30
    coingecko_price_change_pct: float = 5.0

    # Circuit breaker
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_recovery_timeout_s: float = 60.0

    # General
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _validate_live_mode(self) -> "Settings":
        if self.trading_mode == "live" and not self.live_trading_confirmed:
            raise ValueError("Live trading requires LIVE_TRADING_CONFIRMED=true")
        return self
```

**Step 4: Verify pass**

Run: `pytest tests/test_config.py -v`

**Step 5: Commit**

```bash
git add src/config/settings.py tests/test_config.py && git commit -m "task-2: config module with pydantic-settings and risk defaults"
```

---

### Task 3: Circuit Breaker

**Files:**
- Create: `src/utils/circuit_breaker.py`
- Create: `tests/test_circuit_breaker.py`

**Step 1: Write failing test**

```python
# tests/test_circuit_breaker.py
"""Tests for async circuit breaker."""
from __future__ import annotations
import asyncio
import pytest
from utils.circuit_breaker import AsyncCircuitBreaker, CircuitOpenError, CircuitState


async def _succeed() -> str:
    return "ok"

async def _fail() -> str:
    raise RuntimeError("boom")

async def _value_error() -> str:
    raise ValueError("bad input")


@pytest.mark.asyncio
async def test_closed_by_default():
    cb = AsyncCircuitBreaker("test")
    assert cb.state == CircuitState.CLOSED

@pytest.mark.asyncio
async def test_success_keeps_closed():
    cb = AsyncCircuitBreaker("test")
    result = await cb.call(_succeed)
    assert result == "ok"
    assert cb.state == CircuitState.CLOSED

@pytest.mark.asyncio
async def test_opens_after_threshold():
    cb = AsyncCircuitBreaker("test", failure_threshold=2, recovery_timeout=60.0)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(_fail)
    assert cb.state == CircuitState.OPEN

@pytest.mark.asyncio
async def test_open_raises_circuit_open_error():
    cb = AsyncCircuitBreaker("test", failure_threshold=1, recovery_timeout=60.0)
    with pytest.raises(RuntimeError):
        await cb.call(_fail)
    with pytest.raises(CircuitOpenError) as exc_info:
        await cb.call(_succeed)
    assert exc_info.value.name == "test"

@pytest.mark.asyncio
async def test_half_open_after_recovery_timeout():
    cb = AsyncCircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
    with pytest.raises(RuntimeError):
        await cb.call(_fail)
    await asyncio.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN

@pytest.mark.asyncio
async def test_half_open_success_resets_to_closed():
    cb = AsyncCircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
    with pytest.raises(RuntimeError):
        await cb.call(_fail)
    await asyncio.sleep(0.15)
    result = await cb.call(_succeed)
    assert result == "ok"
    assert cb.state == CircuitState.CLOSED

@pytest.mark.asyncio
async def test_excluded_exceptions_pass_through():
    cb = AsyncCircuitBreaker("test", failure_threshold=1, excluded_exceptions=(ValueError,))
    with pytest.raises(ValueError):
        await cb.call(_value_error)
    assert cb.state == CircuitState.CLOSED

@pytest.mark.asyncio
async def test_reset():
    cb = AsyncCircuitBreaker("test", failure_threshold=1, recovery_timeout=60.0)
    with pytest.raises(RuntimeError):
        await cb.call(_fail)
    assert cb.state == CircuitState.OPEN
    await cb.reset()
    assert cb.state == CircuitState.CLOSED
```

**Step 2:** Run: `pytest tests/test_circuit_breaker.py -v` — Expected: FAIL

**Step 3: Write implementation**

```python
# src/utils/circuit_breaker.py
"""Async circuit breaker — adapted from cerid-ai."""
from __future__ import annotations
import asyncio
import time
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any, TypeVar

T = TypeVar("T")

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitOpenError(Exception):
    def __init__(self, name: str, retry_after: float) -> None:
        self.name = name
        self.retry_after = retry_after
        super().__init__(f"Circuit '{name}' is open, retry after {retry_after:.0f}s")

class AsyncCircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 3, recovery_timeout: float = 60.0,
                 excluded_exceptions: tuple[type[Exception], ...] = ()) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.excluded_exceptions = excluded_exceptions
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                return CircuitState.HALF_OPEN
        return self._state

    async def call(self, fn: Callable[..., Coroutine[Any, Any, T]], *args: Any, **kwargs: Any) -> T:
        current_state = self.state
        if current_state == CircuitState.OPEN:
            remaining = self.recovery_timeout - (time.monotonic() - self._last_failure_time)
            raise CircuitOpenError(self.name, max(0, remaining))
        try:
            result = await fn(*args, **kwargs)
        except Exception as exc:
            if isinstance(exc, self.excluded_exceptions):
                raise
            await self._on_failure()
            raise
        else:
            await self._on_success()
            return result

    async def _on_success(self) -> None:
        async with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN

    async def reset(self) -> None:
        async with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            self._last_failure_time = 0
```

**Step 4:** Run: `pytest tests/test_circuit_breaker.py -v` — Expected: PASS

**Step 5:** `git add src/utils/circuit_breaker.py tests/test_circuit_breaker.py && git commit -m "task-3: async circuit breaker with excluded exceptions and auto-recovery"`

---

### Task 4: xAI Client

**Files:**
- Create: `src/utils/xai_client.py`
- Create: `tests/test_xai_client.py`

**Step 1: Write failing test**

```python
# tests/test_xai_client.py
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from utils.xai_client import XAIClient

@pytest.fixture
def client() -> XAIClient:
    return XAIClient(api_key="test-key", model="grok-test")

@pytest.mark.asyncio
async def test_chat_sends_messages(client: XAIClient):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hello from Grok"
    with patch.object(client._async_client.chat.completions, "create",
                      new_callable=AsyncMock, return_value=mock_response) as mock_create:
        result = await client.chat([{"role": "user", "content": "hi"}])
    assert result == "Hello from Grok"
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["model"] == "grok-test"

@pytest.mark.asyncio
async def test_chat_with_system_prompt(client: XAIClient):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "response"
    with patch.object(client._async_client.chat.completions, "create",
                      new_callable=AsyncMock, return_value=mock_response) as mock_create:
        await client.chat([{"role": "user", "content": "analyze BTC"}],
                         system_prompt="You are a trading analyst.")
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["messages"][0]["role"] == "system"

@pytest.mark.asyncio
async def test_chat_returns_none_on_empty_response(client: XAIClient):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = None
    with patch.object(client._async_client.chat.completions, "create",
                      new_callable=AsyncMock, return_value=mock_response):
        result = await client.chat([{"role": "user", "content": "hi"}])
    assert result is None

def test_client_uses_xai_base_url():
    c = XAIClient(api_key="key", model="model")
    assert "x.ai" in str(c._async_client.base_url)
```

**Step 2:** Run: `pytest tests/test_xai_client.py -v` — Expected: FAIL

**Step 3: Write implementation**

```python
# src/utils/xai_client.py
"""xAI / Grok client wrapper using OpenAI-compatible API."""
from __future__ import annotations
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)
XAI_BASE_URL = "https://api.x.ai/v1"

class XAIClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.model = model
        self._async_client = AsyncOpenAI(api_key=api_key, base_url=XAI_BASE_URL)

    async def chat(self, messages: list[dict[str, str]], *, system_prompt: str | None = None,
                   temperature: float = 0.7, max_tokens: int = 2048) -> str | None:
        final_messages: list[dict[str, str]] = []
        if system_prompt:
            final_messages.append({"role": "system", "content": system_prompt})
        final_messages.extend(messages)
        response = await self._async_client.chat.completions.create(
            model=self.model, messages=final_messages, temperature=temperature, max_tokens=max_tokens)
        content = response.choices[0].message.content
        return content if content else None
```

**Step 4:** Run: `pytest tests/test_xai_client.py -v` — Expected: PASS

**Step 5:** `git add src/utils/xai_client.py tests/test_xai_client.py && git commit -m "task-4: xAI client wrapper for Grok endpoint"`

---

### Task 5: Cerid Client

**Files:**
- Create: `src/utils/cerid_client.py`
- Create: `tests/test_cerid_client.py`

**Step 1: Write failing test**

```python
# tests/test_cerid_client.py
from __future__ import annotations
import httpx
import pytest
import respx
from utils.cerid_client import CeridClient

@pytest.fixture
def client() -> CeridClient:
    return CeridClient(base_url="http://localhost:8888")

@respx.mock
@pytest.mark.asyncio
async def test_query_kb_returns_answer(client: CeridClient):
    respx.post("http://localhost:8888/tools/pkb_query").mock(
        return_value=httpx.Response(200, json={"result": {"answer": "BTC funding is positive", "sources": ["doc-1"], "confidence": 0.92}}))
    result = await client.query_kb("What is BTC funding rate?")
    assert result["answer"] == "BTC funding is positive"
    assert result["confidence"] == 0.92

@respx.mock
@pytest.mark.asyncio
async def test_health_check(client: CeridClient):
    respx.get("http://localhost:8888/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    result = await client.health_check()
    assert result is True

@respx.mock
@pytest.mark.asyncio
async def test_health_check_returns_false_on_failure(client: CeridClient):
    respx.get("http://localhost:8888/health").mock(return_value=httpx.Response(503))
    result = await client.health_check()
    assert result is False

@pytest.mark.asyncio
async def test_close_client(client: CeridClient):
    await client.close()
    assert client._client.is_closed
```

**Step 2:** Run: `pytest tests/test_cerid_client.py -v` — Expected: FAIL

**Step 3: Write implementation**

```python
# src/utils/cerid_client.py
"""HTTP client for the Cerid AI MCP API."""
from __future__ import annotations
import logging
from typing import Any
import httpx

logger = logging.getLogger(__name__)

class CeridClient:
    def __init__(self, base_url: str = "http://localhost:8888", timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            timeout=timeout)

    async def query_kb(self, query: str, collection: str = "default") -> dict[str, Any]:
        payload = {"query": query, "collection": collection}
        response = await self._client.post("/tools/pkb_query", json=payload)
        response.raise_for_status()
        return response.json()["result"]

    async def health_check(self) -> bool:
        try:
            response = await self._client.get("/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()
```

**Step 4:** Run: `pytest tests/test_cerid_client.py -v` — Expected: PASS

**Step 5:** `git add src/utils/cerid_client.py tests/test_cerid_client.py && git commit -m "task-5: Cerid MCP HTTP client with health check and KB query"`

---

### Task 6: Docker Setup

**Files:**
- Create: `docker-compose.yml`, `Dockerfile`, `scripts/start-trading.sh`, `scripts/validate-env.sh`

No TDD — infrastructure config.

```yaml
# docker-compose.yml
services:
  trading-agent:
    container_name: cerid-trading-agent
    build: { context: ., dockerfile: Dockerfile }
    ports: ["127.0.0.1:${AGENT_PORT:-8090}:8090"]
    env_file: [.env]
    volumes: [./logs:/app/logs]
    networks: [llm-network]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: '1.0', memory: 4G } } }
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://127.0.0.1:8090/health || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 20s

networks:
  llm-network:
    external: true
```

```dockerfile
# Dockerfile
FROM python:3.11-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e "." 2>/dev/null || pip install --no-cache-dir .
COPY src/ src/
EXPOSE 8090
CMD ["python", "-m", "src.main"]
```

```bash
#!/usr/bin/env bash
# scripts/start-trading.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"
bash "$SCRIPT_DIR/validate-env.sh"
BUILD_FLAG=""
[[ "${1:-}" == "--build" ]] && BUILD_FLAG="--build"
docker network inspect llm-network >/dev/null 2>&1 || docker network create llm-network
echo "Starting cerid-trading-agent..."
docker compose up -d $BUILD_FLAG
echo "Trading Agent: http://127.0.0.1:${AGENT_PORT:-8090}"
```

```bash
#!/usr/bin/env bash
# scripts/validate-env.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PASS=0; FAIL=0
check() { if eval "$2" >/dev/null 2>&1; then echo "  ✓ $1"; ((PASS++)); else echo "  ✗ $1"; ((FAIL++)); fi; }
echo "cerid-trading-agent pre-flight checks"
check "Docker running" "docker info"
check ".env exists" "test -f $PROJECT_DIR/.env"
check "XAI_API_KEY set" "grep -q '^XAI_API_KEY=.' $PROJECT_DIR/.env"
check "TRADING_MODE set" "grep -q '^TRADING_MODE=' $PROJECT_DIR/.env"
check "Python 3.11+" "python3 --version | grep -E '3\.(1[1-9]|[2-9][0-9])'"
check "llm-network exists" "docker network inspect llm-network"
check "Cerid MCP reachable" "curl -sf http://localhost:8888/health"
echo ""; echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -gt 0 ]] && { echo "Fix failures before starting."; exit 1; }
```

**Verification:** `chmod +x scripts/*.sh && python -m pytest tests/test_docker_config.py -v`

**Commit:** `git add docker-compose.yml Dockerfile scripts/ && git commit -m "task-6: Docker setup with compose, Dockerfile, start and validate scripts"`

---

## Phase 2: Core Agents & Cerid Integration (Days 4–8)

### Task 7: TradingState

**Files:**
- Create: `src/graph/state.py`
- Create: `tests/test_state.py`

**Step 1: Write failing test**

```python
# tests/test_state.py
from graph.state import TradingState, Signal, RiskAssessment, ExecutionResult

def test_signal_creation():
    sig = Signal(source="sentinel", asset="BTC", direction="long", confidence=0.85, metadata={"liquidation_usd": 50000})
    assert sig["source"] == "sentinel"
    assert sig["direction"] == "long"

def test_risk_assessment_creation():
    ra = RiskAssessment(approved=True, position_size_usd=250.0, reason="Within limits", drawdown_pct=0.02)
    assert ra["approved"] is True

def test_execution_result_creation():
    er = ExecutionResult(order_id="ord-123", status="filled", fill_price=95000.0, fill_qty=0.003, slippage_bps=2.5)
    assert er["status"] == "filled"

def test_trading_state_minimal():
    state: TradingState = {"cycle_id": "cycle-001", "mode": "paper", "signal": None, "kb_context": None,
        "risk_assessment": None, "execution_result": None, "reflection": None, "daily_pnl": 0.0, "errors": []}
    assert state["mode"] == "paper"
```

**Step 2:** Run: `pytest tests/test_state.py -v` — Expected: FAIL

**Step 3: Write implementation**

```python
# src/graph/state.py
"""LangGraph state definitions for the trading pipeline."""
from __future__ import annotations
from typing import Any, TypedDict

class Signal(TypedDict):
    source: str
    asset: str
    direction: str  # "long" | "short"
    confidence: float
    metadata: dict[str, Any]

class RiskAssessment(TypedDict):
    approved: bool
    position_size_usd: float
    reason: str
    drawdown_pct: float

class ExecutionResult(TypedDict):
    order_id: str
    status: str  # "filled" | "partial" | "rejected"
    fill_price: float
    fill_qty: float
    slippage_bps: float

class TradingState(TypedDict):
    cycle_id: str
    mode: str  # "paper" | "live"
    signal: Signal | None
    kb_context: str | None
    risk_assessment: RiskAssessment | None
    execution_result: ExecutionResult | None
    reflection: str | None
    daily_pnl: float
    errors: list[str]
```

**Step 4:** Run: `pytest tests/test_state.py -v` — Expected: PASS

**Step 5:** `git add src/graph/state.py tests/test_state.py && git commit -m "task-7: TradingState TypedDict with Signal, RiskAssessment, ExecutionResult"`

---

### Task 8: Exchange Base

**Files:**
- Create: `src/exchanges/base.py`
- Create: `tests/test_exchange_base.py`

**Step 1: Write failing test**

```python
# tests/test_exchange_base.py
import pytest
from exchanges.base import ExchangeBase, OrderSide, OrderType, Order, Position

def test_cannot_instantiate_exchange_base():
    with pytest.raises(TypeError):
        ExchangeBase()

def test_order_creation():
    order = Order(exchange="hyperliquid", asset="BTC", side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=0.001, price=95000.0)
    assert order.exchange == "hyperliquid"

def test_position_notional():
    pos = Position(exchange="hyperliquid", asset="BTC", side=OrderSide.BUY, quantity=0.005, entry_price=94000.0, unrealized_pnl=50.0)
    assert pos.notional_usd == pytest.approx(470.0)

def test_exchange_base_has_required_abstract_methods():
    import inspect
    abstract_methods = {name for name, method in inspect.getmembers(ExchangeBase) if getattr(method, "__isabstractmethod__", False)}
    assert {"get_orderbook", "place_order", "get_positions", "subscribe_liquidations", "cancel_order"}.issubset(abstract_methods)
```

**Step 2:** Run: `pytest tests/test_exchange_base.py -v` — Expected: FAIL

**Step 3: Write implementation**

```python
# src/exchanges/base.py
"""Abstract exchange interface."""
from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Any

class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"

class OrderType(Enum):
    LIMIT = "limit"
    MARKET = "market"

@dataclass
class Order:
    exchange: str; asset: str; side: OrderSide; order_type: OrderType; quantity: float; price: float | None = None; client_order_id: str | None = None

@dataclass
class Position:
    exchange: str; asset: str; side: OrderSide; quantity: float; entry_price: float; unrealized_pnl: float
    @property
    def notional_usd(self) -> float: return self.quantity * self.entry_price

@dataclass
class OrderbookSnapshot:
    asset: str; best_bid: float; best_ask: float; bid_size: float; ask_size: float; timestamp_ms: int

@dataclass
class LiquidationEvent:
    asset: str; side: str; size_usd: float; price: float; timestamp_ms: int

class ExchangeBase(ABC):
    @abstractmethod
    async def get_orderbook(self, asset: str) -> OrderbookSnapshot: ...
    @abstractmethod
    async def place_order(self, order: Order) -> dict[str, Any]: ...
    @abstractmethod
    async def cancel_order(self, order_id: str, asset: str) -> bool: ...
    @abstractmethod
    async def get_positions(self) -> list[Position]: ...
    @abstractmethod
    async def subscribe_liquidations(self, assets: list[str]) -> AsyncIterator[LiquidationEvent]: ...
```

**Step 4:** Run: `pytest tests/test_exchange_base.py -v` — Expected: PASS

**Step 5:** `git add src/exchanges/base.py tests/test_exchange_base.py && git commit -m "task-8: abstract exchange interface with Order, Position, and Orderbook types"`

---

### Task 9-10: Exchange Connectors (Hyperliquid + Polymarket)

See full implementations in the Phase 1-2 agent output. Each wraps the respective SDK with async executors, mock-friendly architecture, and complete test suites. Key patterns:

- **Hyperliquid**: `run_in_executor` for sync SDK calls, WebSocket liquidation stream, `l2_snapshot` for orderbook
- **Polymarket**: `py_clob_client.ClobClient` + `GammaClient` for market discovery, order placement via CLOB

**Commits:**
```bash
git commit -m "task-9: Hyperliquid exchange connector with orderbook, orders, positions, liquidation stream"
git commit -m "task-10: Polymarket exchange connector with CLOB trading and Gamma API discovery"
```

---

### Task 11: Sentinel Agent

**Files:**
- Create: `src/agents/sentinel.py`
- Create: `tests/test_sentinel.py`

Multi-source scanner with `_scan_liquidations()` ($25K threshold), `_scan_price_moves()` (CoinGecko 5% change), `_scan_polymarket()`. Returns highest-confidence Signal. Full test suite with mocked data sources.

**Key implementation detail:**
```python
# Liquidation → opposite direction signal
direction = "short" if liq["side"] == "long" else "long"
# Confidence scales with size: min(0.95, 0.5 + size/200_000)
confidence = min(0.95, 0.5 + (liq["size_usd"] / 200_000))
```

**Commit:** `git commit -m "task-11: Sentinel agent with liquidation, CoinGecko, and Polymarket scanners"`

---

### Task 12: Oracle Agent

**Files:**
- Create: `src/agents/oracle.py`
- Create: `tests/test_oracle.py`

Wraps `CeridClient.query_kb()` with signal-aware query building. Handles cerid-ai downtime gracefully (degraded mode with error logging). Includes KB confidence in context string.

**Commit:** `git commit -m "task-12: Oracle agent wrapping Cerid KB for signal enrichment"`

---

### Task 13: Ghost Agent

**Files:**
- Create: `src/agents/ghost.py`
- Create: `tests/test_ghost.py`

Iceberg order execution with configurable `max_slices` and `min_slice_usd`. Computes slippage in basis points. Paper mode simulates fills against current orderbook.

**Key implementation detail:**
```python
def _compute_slices(self, total_qty: float, total_usd: float) -> list[float]:
    max_slices_by_value = max(1, int(total_usd / self.min_slice_usd))
    n_slices = min(self.max_slices, max_slices_by_value)
    return [total_qty / n_slices] * n_slices
```

**Commit:** `git commit -m "task-13: Ghost agent with iceberg order splitting for paper trade execution"`

---

### Task 14: LangGraph Workflow

**Files:**
- Create: `src/graph/workflow.py`, `src/graph/conditions.py`
- Create: `tests/test_workflow.py`

**Conditions:**
```python
def should_query_oracle(state) -> str:
    return "oracle" if state.get("signal") is not None else "end"

def should_execute(state) -> str:
    risk = state.get("risk_assessment")
    return "ghost" if risk is not None and risk["approved"] else "end"

def should_reflect(state) -> str:
    return "reflection" if state.get("execution_result") is not None else "end"
```

**Workflow:** `StateGraph(TradingState)` with nodes wired via conditional edges:
```
sentinel →[signal?]→ oracle → risk →[approved?]→ ghost →[executed?]→ reflection → END
```

Full test suite covers: no-signal early exit, rejected-risk skip, and full pipeline execution.

**Commit:** `git commit -m "task-14: LangGraph workflow with conditional edges for Sentinel→Oracle→Risk→Ghost→Reflection"`

---

## Phase 3: Risk Engine & Strategies (Days 9–13)

### Task 15: VPIN Calculator

**Files:**
- Create: `src/risk/vpin.py`, `src/risk/__init__.py`
- Create: `tests/risk/test_vpin.py`

**Step 1: Write failing test**

```python
# tests/risk/test_vpin.py
import pytest
from risk.vpin import VPINCalculator

def test_vpin_balanced_flow():
    calc = VPINCalculator(bucket_size=100)
    trades = [{"price": 100, "size": 50, "side": "buy"}, {"price": 100, "size": 50, "side": "sell"}]
    vpin = calc.compute(trades)
    assert vpin == pytest.approx(0.0)

def test_vpin_all_buys():
    calc = VPINCalculator(bucket_size=100)
    trades = [{"price": 100, "size": 100, "side": "buy"}]
    vpin = calc.compute(trades)
    assert vpin == pytest.approx(1.0)

def test_vpin_mixed():
    calc = VPINCalculator(bucket_size=100)
    trades = [{"price": 100, "size": 80, "side": "buy"}, {"price": 100, "size": 20, "side": "sell"}]
    vpin = calc.compute(trades)
    assert vpin == pytest.approx(0.6)

def test_vpin_empty_trades():
    calc = VPINCalculator(bucket_size=100)
    vpin = calc.compute([])
    assert vpin == 0.0

def test_is_toxic_above_threshold():
    calc = VPINCalculator(bucket_size=100, toxicity_threshold=0.6)
    trades = [{"price": 100, "size": 90, "side": "buy"}, {"price": 100, "size": 10, "side": "sell"}]
    assert calc.is_toxic(trades) is True

def test_is_toxic_below_threshold():
    calc = VPINCalculator(bucket_size=100, toxicity_threshold=0.6)
    trades = [{"price": 100, "size": 55, "side": "buy"}, {"price": 100, "size": 45, "side": "sell"}]
    assert calc.is_toxic(trades) is False
```

**Step 2:** Run: `pytest tests/risk/test_vpin.py -v` — Expected: FAIL

**Step 3: Write implementation**

```python
# src/risk/__init__.py
```

```python
# src/risk/vpin.py
"""VPIN — Volume-Synchronized Probability of Informed Trading."""
from __future__ import annotations
from typing import Any

class VPINCalculator:
    def __init__(self, bucket_size: float = 1000, toxicity_threshold: float = 0.6) -> None:
        self.bucket_size = bucket_size
        self.toxicity_threshold = toxicity_threshold

    def compute(self, trades: list[dict[str, Any]]) -> float:
        if not trades:
            return 0.0
        v_buy = sum(t["size"] for t in trades if t["side"] == "buy")
        v_sell = sum(t["size"] for t in trades if t["side"] == "sell")
        total = v_buy + v_sell
        if total == 0:
            return 0.0
        return abs(v_buy - v_sell) / total

    def is_toxic(self, trades: list[dict[str, Any]]) -> bool:
        return self.compute(trades) > self.toxicity_threshold
```

**Step 4:** Run: `pytest tests/risk/test_vpin.py -v` — Expected: PASS

**Step 5:** `git add src/risk/ tests/risk/ && git commit -m "task-15: VPIN calculator with toxicity threshold"`

---

### Task 16: Value at Risk

**Files:**
- Create: `src/risk/var.py`
- Create: `tests/risk/test_var.py`

**Step 1: Write failing test**

```python
# tests/risk/test_var.py
import pytest
from risk.var import VaRCalculator

def test_parametric_var():
    calc = VaRCalculator()
    returns = [0.01, -0.02, 0.015, -0.01, 0.005, -0.025, 0.02, -0.015, 0.01, -0.005]
    var = calc.parametric(returns, portfolio_value=10000, confidence=0.95)
    assert var > 0
    assert var < 10000

def test_historical_var():
    calc = VaRCalculator()
    returns = [0.01, -0.02, 0.015, -0.01, 0.005, -0.025, 0.02, -0.015, 0.01, -0.005] * 10
    var = calc.historical(returns, portfolio_value=10000, confidence=0.95)
    assert var > 0

def test_parametric_var_empty_returns():
    calc = VaRCalculator()
    var = calc.parametric([], portfolio_value=10000)
    assert var == 0.0

def test_higher_confidence_higher_var():
    calc = VaRCalculator()
    returns = [0.01, -0.02, 0.015, -0.01, 0.005, -0.025, 0.02, -0.015, 0.01, -0.005]
    var_95 = calc.parametric(returns, portfolio_value=10000, confidence=0.95)
    var_99 = calc.parametric(returns, portfolio_value=10000, confidence=0.99)
    assert var_99 > var_95
```

**Step 2:** Run: `pytest tests/risk/test_var.py -v` — Expected: FAIL

**Step 3: Write implementation**

```python
# src/risk/var.py
"""Value at Risk — parametric and historical methods."""
from __future__ import annotations
import math
import statistics

# Z-scores for common confidence levels
Z_SCORES = {0.90: 1.282, 0.95: 1.645, 0.99: 2.326}

class VaRCalculator:
    def parametric(self, returns: list[float], portfolio_value: float, confidence: float = 0.95, horizon_days: float = 1.0) -> float:
        if len(returns) < 2:
            return 0.0
        sigma = statistics.stdev(returns)
        z = Z_SCORES.get(confidence, 1.645)
        return portfolio_value * z * sigma * math.sqrt(horizon_days)

    def historical(self, returns: list[float], portfolio_value: float, confidence: float = 0.95) -> float:
        if len(returns) < 2:
            return 0.0
        sorted_returns = sorted(returns)
        index = int((1 - confidence) * len(sorted_returns))
        index = max(0, min(index, len(sorted_returns) - 1))
        return abs(sorted_returns[index]) * portfolio_value
```

**Step 4:** Run: `pytest tests/risk/test_var.py -v` — Expected: PASS

**Step 5:** `git add src/risk/var.py tests/risk/test_var.py && git commit -m "task-16: VaR calculator with parametric and historical methods"`

---

### Task 17: Kelly Criterion

**Files:**
- Create: `src/risk/kelly.py`
- Create: `tests/risk/test_kelly.py`

**Step 1: Write failing test**

```python
# tests/risk/test_kelly.py
import pytest
from risk.kelly import KellyCriterion

def test_classic_kelly():
    kc = KellyCriterion()
    f = kc.classic(win_prob=0.6, win_loss_ratio=1.5)
    assert f == pytest.approx((0.6 * 1.5 - 0.4) / 1.5, rel=1e-3)

def test_empirical_kelly():
    kc = KellyCriterion()
    f = kc.empirical(win_prob=0.6, win_loss_ratio=1.5, cv_edge=0.3)
    classic = (0.6 * 1.5 - 0.4) / 1.5
    assert f == pytest.approx(classic * (1 - 0.3), rel=1e-3)

def test_kelly_negative_edge():
    kc = KellyCriterion()
    f = kc.classic(win_prob=0.3, win_loss_ratio=1.0)
    assert f < 0  # Negative edge → don't trade

def test_position_size_respects_cap():
    kc = KellyCriterion(max_fraction=0.25)
    size = kc.position_size(win_prob=0.9, win_loss_ratio=3.0, bankroll=1000)
    assert size <= 250.0

def test_position_size_zero_on_negative_edge():
    kc = KellyCriterion()
    size = kc.position_size(win_prob=0.3, win_loss_ratio=1.0, bankroll=1000)
    assert size == 0.0
```

**Step 2:** Run: `pytest tests/risk/test_kelly.py -v` — Expected: FAIL

**Step 3: Write implementation**

```python
# src/risk/kelly.py
"""Kelly Criterion with empirical adjustment."""
from __future__ import annotations

class KellyCriterion:
    def __init__(self, max_fraction: float = 0.25) -> None:
        self.max_fraction = max_fraction

    def classic(self, win_prob: float, win_loss_ratio: float) -> float:
        q = 1 - win_prob
        return (win_prob * win_loss_ratio - q) / win_loss_ratio

    def empirical(self, win_prob: float, win_loss_ratio: float, cv_edge: float = 0.0) -> float:
        f = self.classic(win_prob, win_loss_ratio)
        return f * (1 - cv_edge)

    def position_size(self, win_prob: float, win_loss_ratio: float, bankroll: float,
                      cv_edge: float = 0.0) -> float:
        f = self.empirical(win_prob, win_loss_ratio, cv_edge)
        if f <= 0:
            return 0.0
        capped = min(f, self.max_fraction)
        return round(capped * bankroll, 2)
```

**Step 4:** Run: `pytest tests/risk/test_kelly.py -v` — Expected: PASS

**Step 5:** `git add src/risk/kelly.py tests/risk/test_kelly.py && git commit -m "task-17: Kelly criterion with empirical adjustment and cap"`

---

### Task 18: Trading Circuit Breakers

**Files:**
- Create: `src/risk/circuit_breakers.py`
- Create: `tests/risk/test_circuit_breakers.py`

**Step 1: Write failing test**

```python
# tests/risk/test_circuit_breakers.py
import pytest
from risk.circuit_breakers import TradingCircuitBreaker, TradingHalted

def test_passes_when_all_ok():
    cb = TradingCircuitBreaker(vpin_threshold=0.6, max_daily_loss_pct=0.02, max_trade_usd=50)
    cb.check(vpin=0.3, daily_pnl=-5.0, portfolio_value=1000, trade_usd=40)

def test_halts_on_high_vpin():
    cb = TradingCircuitBreaker(vpin_threshold=0.6, max_daily_loss_pct=0.02, max_trade_usd=50)
    with pytest.raises(TradingHalted, match="VPIN"):
        cb.check(vpin=0.65, daily_pnl=0, portfolio_value=1000, trade_usd=30)

def test_halts_on_daily_loss():
    cb = TradingCircuitBreaker(vpin_threshold=0.6, max_daily_loss_pct=0.02, max_trade_usd=50)
    with pytest.raises(TradingHalted, match="daily loss"):
        cb.check(vpin=0.3, daily_pnl=-25.0, portfolio_value=1000, trade_usd=30)

def test_halts_on_trade_too_large():
    cb = TradingCircuitBreaker(vpin_threshold=0.6, max_daily_loss_pct=0.02, max_trade_usd=50)
    with pytest.raises(TradingHalted, match="trade size"):
        cb.check(vpin=0.3, daily_pnl=0, portfolio_value=1000, trade_usd=60)
```

**Step 2:** Run: `pytest tests/risk/test_circuit_breakers.py -v` — Expected: FAIL

**Step 3: Write implementation**

```python
# src/risk/circuit_breakers.py
"""Trading-specific circuit breakers."""
from __future__ import annotations

class TradingHalted(Exception):
    pass

class TradingCircuitBreaker:
    def __init__(self, vpin_threshold: float = 0.6, max_daily_loss_pct: float = 0.02, max_trade_usd: float = 50.0) -> None:
        self.vpin_threshold = vpin_threshold
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_trade_usd = max_trade_usd

    def check(self, vpin: float, daily_pnl: float, portfolio_value: float, trade_usd: float) -> None:
        if vpin > self.vpin_threshold:
            raise TradingHalted(f"VPIN {vpin:.3f} exceeds threshold {self.vpin_threshold}")
        max_loss = portfolio_value * self.max_daily_loss_pct
        if daily_pnl < 0 and abs(daily_pnl) > max_loss:
            raise TradingHalted(f"Exceeded daily loss limit: ${abs(daily_pnl):.2f} > ${max_loss:.2f}")
        if trade_usd > self.max_trade_usd:
            raise TradingHalted(f"Per-trade size ${trade_usd:.2f} exceeds max ${self.max_trade_usd:.2f}")
```

**Step 4:** Run: `pytest tests/risk/test_circuit_breakers.py -v` — Expected: PASS

**Step 5:** `git add src/risk/circuit_breakers.py tests/risk/test_circuit_breakers.py && git commit -m "task-18: trading circuit breakers with VPIN, daily loss, and per-trade limits"`

---

### Tasks 19-22: Risk Overseer Agent, Strategies, Backtest Engine, Autoresearch

These follow the same TDD pattern. Key implementations:

- **Task 19 (Risk Overseer):** LangGraph node wrapping VPIN + Kelly + VaR + circuit breakers
- **Task 20 (BaseStrategy + Herd Fading):** Abstract `execute(state) → Signal` + 80% consensus detection with ATR stops
- **Task 21 (Avellaneda-Stoikov + Liquidation Arb):** Inventory-adjusted δ formula + $25K cascade trigger
- **Task 22 (Backtest + Autoresearch):** Backtrader adapter + param sweep with Sharpe ranking

---

## Phase 4: ML Model & Dashboard (Days 14–18)

### Tasks 23-25: ML Predictor + MC Dropout + Training Pipeline

**PyTorch model:**
```python
class MarketPredictor(nn.Module):
    def __init__(self, n_features=38, seq_len=60):
        super().__init__()
        self.conv1 = nn.Conv1d(n_features, 32, kernel_size=3, padding=1)
        self.lstm = nn.LSTM(32, 50, batch_first=True)
        self.dropout = nn.Dropout(0.2)
        self.fc = nn.Linear(50, 1)

    def forward(self, x):
        x = torch.relu(self.conv1(x))
        x = x.permute(0, 2, 1)
        _, (h, _) = self.lstm(x)
        x = self.dropout(h[-1])
        return torch.sigmoid(self.fc(x))
```

**MC Dropout inference:** 50 forward passes with dropout enabled → mean > 0.7 AND std < 0.1 → trade

### Tasks 26-28: Reflection Agent + Streamlit Dashboard + Audit Trail

**Dashboard pages:** Herd gauge, VaR heatmap, live trades, P&L chart, audit trail with Redis event log, model confidence with MC Dropout intervals.

---

## Phase 5: Security, CI & Live Readiness (Days 19–25)

### Task 29: Wallet Manager

```python
class WalletManager:
    def __init__(self, cap: float = 500.0):
        self.cap = cap; self.balance = 0.0; self.total_deployed = 0.0

    def can_trade(self, amount: float) -> bool:
        return amount <= self.balance and self.total_deployed + amount <= self.cap

    def enforce_cap(self, amount: float) -> None:
        if not self.can_trade(amount): raise WalletCapExceeded(...)
```

### Task 30: Docker Security

Hardened `docker-compose.yml` with `read_only: true`, `no-new-privileges`, Docker secrets for wallet keys, tmpfs for /tmp, resource limits.

### Task 31: Prompt Templates

7 structured prompt templates in `prompts/` directory — sentinel_herd_detect, oracle_kb_query, risk_kelly_calc, ghost_execution, reflection_scoring, correlation_anomaly, sentiment_arb.

### Task 32: GitHub Actions CI

5-job pipeline: lint (ruff) → typecheck (mypy) → test (pytest) → security (bandit) → docker build.

### Task 33: Start Script

Full pre-flight (Python version, Docker, cerid-ai connectivity, age key, ports) → sequential startup (Redis → agent → dashboard) → health polling.

### Task 34: CLAUDE.md

Project-specific instructions with architecture overview, hard rules (NEVER exceed $500 cap, NEVER trade when VPIN > 0.6), startup commands, conventions.

---

## Verification Plan

1. **Phase 1**: `docker compose up` → health checks green, `curl localhost:8888/health` from trading container succeeds
2. **Phase 2**: Run full LangGraph cycle with mocked Sentinel signal → Ghost paper trade (iceberg split visible in logs) → Redis audit log entry created
3. **Phase 3**: Backtest all 4 strategies against 30 days historical data → Sharpe >1.5, drawdown <2%. Autoresearch selects best params.
4. **Phase 4**: Streamlit dashboard loads with live paper trading data, ML model inference <100ms, MC Dropout confidence intervals displayed
5. **Phase 5**: 48-hour paper trading soak → circuit breakers tested → live $15 positions → daily reflection loop running
