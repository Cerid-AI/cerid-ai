# Contributing to Cerid AI

Thank you for your interest in contributing to Cerid AI!

## Development Setup

### Prerequisites
- Python 3.11+
- Node.js 20+ (for React GUI development)
- Docker & Docker Compose V2

### Quick Start

```bash
# Clone and set up
git clone https://github.com/YOUR_USERNAME/cerid-ai.git
cd cerid-ai

# One-command bootstrap (creates network, copies .env, starts services)
./scripts/setup.sh

# Or manually:
cp .env.example .env          # Edit with your API keys
docker network create llm-network
./scripts/start-cerid.sh      # Starts all 5 service groups
```

**Services after startup:**
- React GUI: `http://localhost:3000`
- MCP Server API: `http://localhost:8888`
- API Docs: `http://localhost:8888/docs`

### Running Tests

```bash
# Unit tests (backend, ~1941 tests)
cd src/mcp
pip install pytest pytest-asyncio httpx
pytest tests/ -v

# Frontend tests (~611 tests)
cd src/web && npx vitest run

# All checks (lint + typecheck + tests)
make check-all

# Monte Carlo retrieval evaluation
make test-eval

# E2E pipeline integration tests (requires running Docker stack)
python -m pytest src/mcp/tests/

# RAG resilience testing (circuit breakers, degradation paths)
python -m pytest tests/test_rag_resilience.py -v
```

### Linting

```bash
# Python
pip install ruff
ruff check src/mcp/

# TypeScript
cd src/web && npx tsc --noEmit
```

### React GUI Development

```bash
cd src/web
npm install
npm run dev          # Vite dev server on port 5173
```

The Vite dev server proxies `/api/bifrost` to `localhost:8080` (Bifrost LLM gateway).

## Coding Standards

- **Python 3.11+** with type hints on public functions
- **FastAPI** routers in `src/mcp/routers/` with `APIRouter`
- **React 19** + **Tailwind v4** + **shadcn/ui** (New York style, Zinc base)
- **No hardcoded secrets** — use environment variables via `config.py`
- **Cypher queries** live in `utils/graph.py`, never inline in routers
- **ChromaDB metadata** values must be strings or ints (lists stored as JSON strings)
- Keep functions focused and under ~50 lines where practical
- Add logging for error paths: `logger.warning()` for recoverable, `logger.error()` for failures

## Architecture Overview

```
src/mcp/
  main.py           — FastAPI app setup + lifespan (plugin loading here)
  config.py         — All configuration (single source of truth)
  deps.py           — Database client singletons
  scheduler.py      — APScheduler background jobs
  routers/          — FastAPI endpoint modules
  agents/           — LangGraph agent workflows (10 agents)
  utils/            — Shared utilities (graph, BM25, parsers, features, etc.)
  plugins/          — Plugin system (loader, base classes)
  middleware/       — Auth + rate limiting
  tests/            — pytest test suite (~1941 tests)

src/web/
  src/components/   — React components (layout, chat, kb, monitoring, audit, ui)
  src/hooks/        — Custom React hooks
  src/lib/          — API clients, types, model router
  src/contexts/     — React contexts (KB injection, settings)
```

## Plugin Development

Cerid AI supports plugins for extending functionality. See `src/mcp/plugins/base.py` for abstract base classes.

### Creating a Plugin

1. Create a directory in `src/mcp/plugins/your_plugin/`
2. Add `manifest.json`:
   ```json
   {
     "name": "your_plugin",
     "version": "1.0.0",
     "type": "parser",
     "description": "What your plugin does",
     "tier": "community",
     "requires": []
   }
   ```
3. Add `plugin.py` with a `register()` function:
   ```python
   from utils.parsers import PARSER_REGISTRY

   def register():
       PARSER_REGISTRY[".xyz"] = parse_xyz

   def parse_xyz(file_path):
       return {"text": "...", "file_type": "xyz", "page_count": None}
   ```
4. The plugin is auto-discovered on server startup.

**Plugin types:** `parser` (file parsers), `agent` (workflows), `sync` (sync backends), `middleware`

**Tier gating:** Set `"tier": "pro"` in manifest to require `CERID_TIER=pro` environment variable.

## Pull Request Process

1. Fork the repository and create a feature branch from `main`
2. Make your changes with clear, focused commits
3. Ensure tests pass (`pytest`) and linting is clean (`ruff check`, `tsc --noEmit`)
4. Update documentation if adding new endpoints, MCP tools, or plugins
5. Open a PR with a clear description of what and why

### Compliance Check
- [ ] No Chinese-origin AI models referenced (DeepSeek, Qwen, Alibaba, etc.)
- [ ] Default Ollama model is `llama3.2:3b` (Meta) — not Qwen
- [ ] Run: `grep -rn "deepseek\|qwen\|alibaba" src/ --include="*.py" --include="*.ts"` → 0 results

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
