# Contributing to Cerid AI

Thank you for your interest in contributing to Cerid AI!

## Development Setup

### Prerequisites
- Python 3.11+
- Docker & Docker Compose
- A running `llm-network` Docker bridge network

### Quick Start

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/cerid-ai.git
cd cerid-ai

# Copy environment template
cp src/mcp/.env.example src/mcp/.env
# Edit .env with your values

# Create the Docker network (if it doesn't exist)
docker network create llm-network

# Start all services
cd src/mcp
docker compose up --build -d
```

The MCP server runs on `http://localhost:8888`. The Streamlit dashboard runs on `http://localhost:8501`.

### Running Tests

```bash
cd src/mcp
pip install -r requirements.txt
pip install pytest pytest-asyncio httpx
pytest tests/
```

### Linting

```bash
pip install ruff
ruff check src/mcp/
```

## Coding Standards

- **Python 3.11+** with type hints on public functions
- **FastAPI** routers in `src/mcp/routers/` with `APIRouter`
- **No hardcoded secrets** — use environment variables via `config.py`
- **Cypher queries** live in `utils/graph.py`, never inline in routers
- Keep functions focused and under ~50 lines where practical
- Add logging for error paths: `logger.warning()` for recoverable, `logger.error()` for failures

## Pull Request Process

1. Fork the repository and create a feature branch from `main`
2. Make your changes with clear, focused commits
3. Ensure tests pass and linting is clean
4. Update documentation if you're adding new endpoints or MCP tools
5. Open a PR with a clear description of what and why

## Architecture Overview

```
src/mcp/
  main.py           — FastAPI app setup + lifespan
  config.py         — All configuration (single source of truth)
  deps.py           — Database client singletons
  scheduler.py      — APScheduler background jobs
  routers/          — FastAPI endpoint modules
  agents/           — LangGraph agent workflows
  utils/            — Shared utilities (graph, BM25, parsers, etc.)
  tests/            — pytest test suite
```

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
