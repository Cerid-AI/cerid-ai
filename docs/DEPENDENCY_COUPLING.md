## Cross-Service Version Coupling

Constraints that span multiple files. When updating one side, check the other.

## ChromaDB Client / Server

| Component | File | Current |
|-----------|------|---------|
| Server image | `stacks/infrastructure/docker-compose.yml` | `chromadb/chroma:0.5.23` |
| Client library | `src/mcp/requirements.txt` | `chromadb>=0.5,<0.6` |

**Rule:** Client major.minor must match server. A server upgrade to 0.6.x requires updating the client range.

## spaCy Library / Model

| Component | File | Current |
|-----------|------|---------|
| Library | `src/mcp/requirements.txt` | `spacy>=3.5,<3.9` |
| Model | `src/mcp/Dockerfile` | `en_core_web_sm-3.7.1` |

**Rule:** The model version must be compatible with the installed spaCy major version. Check the [spaCy compatibility table](https://spacy.io/usage/models) before upgrading either.

## Node Version

| Component | File | Current |
|-----------|------|---------|
| Source of truth | `src/web/.nvmrc` | `22` |
| Docker build | `src/web/Dockerfile` | `node:22-alpine3.21` |
| CI | `.github/workflows/ci.yml` | `node-version-file: src/web/.nvmrc` |
| Engine constraint | `src/web/package.json` | `"node": ">=22"` |

**Rule:** `.nvmrc` is the single source of truth. CI reads it via `node-version-file`. Dockerfile and `package.json` must agree.

## Python Version

| Component | File | Current |
|-----------|------|---------|
| Docker build | `src/mcp/Dockerfile` | `python:3.11.14-slim` |
| CI | `.github/workflows/ci.yml` | `python-version: "3.11"` |

**Rule:** Both must use the same Python 3.11.x line. Lock files are generated against 3.11.

## pip-tools Version

| Component | File | Current |
|-----------|------|---------|
| CI lock-sync job | `.github/workflows/ci.yml` | `pip-tools==7.5.3` |
| Local generation | Developer machine | Must match CI version |

**Rule:** The `pip-compile` version used locally must match the CI `lock-sync` job. Version mismatches cause non-deterministic lock files and CI failures.

## Neo4j ↔ APOC Compatibility

| Neo4j Version | APOC Version | Notes |
|---------------|-------------|-------|
| 5.x | 5.x (matching major) | Auto-installed via NEO4J_PLUGINS=["apoc"] |
| 4.4.x | 4.4.x | Legacy, not supported |

APOC is installed automatically by the Neo4j Docker image when `NEO4J_PLUGINS=["apoc"]` is set.
The APOC version must match the Neo4j major version. When upgrading Neo4j, verify APOC compatibility
at https://neo4j.com/labs/apoc/current/.

## Ollama (Optional — Local LLM)

| Component | File | Current |
|-----------|------|---------|
| Feature gate | `src/mcp/config/settings.py` | `OLLAMA_ENABLED` (default: `false`) |
| Proxy router | `src/mcp/routers/ollama_proxy.py` | `/ollama/chat`, `/ollama/models`, `/ollama/pull` |
| Default URL | `.env` / `config/settings.py` | `OLLAMA_URL=http://localhost:11434` |

**Rule:** Ollama is entirely optional. When `OLLAMA_ENABLED=false` (default), all `/ollama/*` endpoints return 503. No Ollama dependency is required in the Docker image or Python requirements. The proxy uses httpx with circuit breaker to communicate with a separately-installed Ollama server.

## Bifrost LLM Gateway

| Component | File | Current |
|-----------|------|---------|
| Docker image | `stacks/bifrost/docker-compose.yml` | `maximhq/bifrost:latest` |
| Config | `stacks/bifrost/config.yaml` | Intent routing, model list |

**Rule:** Bifrost uses `latest` tag. When pinning to a specific version, update both the image tag and verify `config.yaml` compatibility with that release.

## Lock File Workflow

When editing `requirements.txt`:
```bash
cd src/mcp
pip-compile requirements.txt -o requirements.lock --generate-hashes --no-header --allow-unsafe
```

When editing `requirements-dev.txt`:
```bash
cd src/mcp
pip-compile requirements-dev.txt -o requirements-dev.lock --generate-hashes --no-header --allow-unsafe
```

The pre-commit hook and CI `lock-sync` job enforce that lock files stay in sync.

## New Consumer Integration

For a complete 13-step checklist on adding a new cerid-series agent integration (feature flag, domain, consumer registration, endpoints, MCP tools, tests, and documentation), see [`docs/INTEGRATION_GUIDE.md`](INTEGRATION_GUIDE.md).

## CONSUMER_REGISTRY Structure

`CONSUMER_REGISTRY` in `config/settings.py` defines per-consumer access control and rate limiting. Each entry is keyed by the `X-Client-ID` header value:

```python
CONSUMER_REGISTRY = {
    "cli-ingest": {
        "rate_limits": {"/ingest": (30, 60)},
        "allowed_domains": None,
        "strict_domains": False,
        "description": "CLI batch ingestion tool",
    },
    "a2a-agent": {
        "rate_limits": {"/agent/": (20, 60), "/sdk/": (20, 60)},
        "allowed_domains": None,
        "strict_domains": False,
        "description": "A2A protocol remote agents",
    },
    "folder_scanner": {
        "rate_limits": {"/ingest": (20, 60)},
        "allowed_domains": None,
        "strict_domains": False,
        "description": "Folder watcher auto-ingestion",
    },
    "gui": {
        "rate_limits": {"/agent/": (20, 60), "/sdk/": (20, 60), "/ingest": (10, 60)},
        "allowed_domains": None,                       # None = all domains (GUI has full access)
        "strict_domains": False,
        "description": "React GUI",
    },
}
```

- **`rate_limit`:** Requests per minute for this consumer.
- **`allowed_domains`:** List of KB domains the consumer can query. `None` means unrestricted.
- **`strict_domains`:** When `True`, disables `DOMAIN_AFFINITY` bleed into domains not in `allowed_domains`.
- **Default behavior:** Unrecognized `X-Client-ID` values get 10 req/min and full domain access (for backward compatibility).
