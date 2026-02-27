# Cross-Service Version Coupling

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
