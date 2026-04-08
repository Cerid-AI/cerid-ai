# Cerid AI — Dependency Audit

**Date:** 2026-04-05
**Repo:** cerid-ai (v0.82.0)
**MCP Docker Image:** 3.18 GB (was 4.09 GB before Tier 1+2 cleanup) | **Web Docker Image:** 98.7 MB

---

## Dependabot Vulnerabilities (33 alerts)

| Severity | Package | Ecosystem | Source | Action |
|----------|---------|-----------|--------|--------|
| HIGH (4) + MED (8) + LOW (4) | `electron` | npm | `packages/desktop/` | **Remove** — desktop app is unused/experimental |
| HIGH (3) | `tar` | npm | electron transitive | Goes away with electron removal |
| HIGH (1) + MED (1) | `picomatch` | npm | eslint transitive | Update eslint or pin picomatch |
| HIGH (1) + MED (1) | `lodash` | npm | electron transitive | Goes away with electron removal |
| HIGH (1) | `@xmldom/xmldom` | npm | electron transitive | Goes away with electron removal |
| MED (3) | `brace-expansion` | npm | eslint transitive | Update eslint to v9+ |
| MED (1) | `esbuild` | npm | vite transitive | Update vite |
| MED (1) | `langgraph` | pip | direct dependency | Update langgraph or audit msgpack usage |
| LOW (1) | `@tootallnate/once` | npm | electron transitive | Goes away with electron removal |

**Quick win: Removing `packages/desktop/` eliminates 26 of 33 vulnerabilities (79%).**

---

## Python Backend (20 direct deps, 156 transitive)

### ESSENTIAL — Core Functionality

| Dependency | Version | Used By | Verdict |
|------------|---------|---------|---------|
| `fastapi` | >=0.100 | Entire API | Essential |
| `uvicorn[standard]` | >=0.20 | Server runtime | Essential |
| `pydantic` | >=2.0 | All request/response models | Essential |
| `httpx` | >=0.24 | OpenRouter, Ollama, Bifrost, sidecar | Essential |
| `chromadb` | >=0.5 | Vector store (core RAG) | Essential |
| `neo4j` | >=5.0 | Graph DB (relationships, taxonomy) | Essential |
| `redis` | >=4.0 | Cache, audit, semantic cache | Essential |
| `tiktoken` | >=0.5 | Token counting (chunker, metadata) | Essential |
| `pdfplumber` | >=0.10 | PDF parsing (core file type) | Essential |
| `python-multipart` | >=0.0.9 | File upload handling | Essential |

### IMPORTANT — Used But Could Be Lighter

| Dependency | Version | Used By | Size Impact | Recommendation |
|------------|---------|---------|-------------|----------------|
| `langgraph` | >=0.3 | `agents/triage.py` (469 lines, 16 functions) | ~50MB + langchain-core + langsmith | **KEEP** — triage.py builds a real conditional routing graph with error propagation and visualization. Reimplementing would lose graph execution semantics. Lazy-import is sufficient optimization. |
| `pandas` | >=2.0 | `parsers/structured.py` (CSV/Excel parsing) | ~100MB + numpy | **KEEP** — uses pd.read_csv for auto-delimiter detection, encoding fallback, column type inference, df.describe() statistics, and schema summary. These enrich KB artifacts. Already lazy-imported at call site. |
| `cryptography` | >=42 | Only `utils/encryption.py` (optional Fernet encryption) | ~15MB | **LOW** — only used when `CERID_ENCRYPTION_KEY` is set. Could lazy-import but it's a C extension so install cost is fixed. |
| `sentry-sdk[fastapi]` | >=2.35 | Only `main.py` (opt-in telemetry) | ~10MB | **LOW** — already gated behind `ENABLE_SENTRY=true`. Could make it an optional install. |

### OPTIONAL — Can Be Removed from Core

| Dependency | Version | Used By | Size Impact | Recommendation |
|------------|---------|---------|-------------|----------------|
| `pytesseract` | >=0.3.10 | Only `plugins/ocr/plugin.py` (Pro tier) | ~2MB (but tesseract-ocr system pkg is 2.7MB) | **REMOVE from requirements** — OCR is Pro-only plugin, should be plugin-level dep |
| `Pillow` | >=10.0 | Only `plugins/ocr/plugin.py` | ~10MB | **REMOVE from requirements** — same as pytesseract, Pro plugin dep |
| `requests` | >=2.28 | Only CLI scripts (watch_obsidian, watch_ingest, ingest_cli, scan_ingest) | ~5MB | **REMOVE** — scripts already have httpx available; requests is redundant |
| `bcrypt` | >=4.0 | Only `routers/auth.py` (multi-user auth) | ~2MB | **CONDITIONAL** — only needed when CERID_MULTI_USER=true. Move to optional extras. |
| `PyJWT` | >=2.8 | Only `middleware/jwt_auth.py` | ~1MB | **CONDITIONAL** — same as bcrypt, only for multi-user mode |
| `structlog` | >=24 | Only 1 file (`hallucination/startup_self_test.py`) | ~3MB | **REMOVE** — stdlib `logging` used everywhere else. One file can switch. |
| `python-docx` | >=0.8 | Only `parsers/office.py` (DOCX parsing) | ~5MB | **KEEP** — common file format, but could be lazy-imported |
| `openpyxl` | >=3.1 | Only `parsers/office.py` (XLSX parsing) | ~10MB | **KEEP** — common file format, but could be lazy-imported |

### REPLACEABLE — Lighter Alternatives Exist

| Dependency | Current Use | Alternative | Savings |
|------------|-------------|-------------|---------|
| `langgraph` (50MB+) | **PROTECTED** — real conditional routing graph in triage.py (469 lines, 16 functions) | N/A — keep | 0 |
| `pandas` (100MB+) | **PROTECTED** — auto-delimiter, encoding fallback, df.describe() stats | N/A — keep | 0 |
| `requests` (5MB) | 4 CLI scripts | `httpx` (already installed) | ~5MB |
| `structlog` (3MB) | 1 file | `logging` stdlib | ~3MB |
| `jinja2` (3MB) | Model prompt templates (1 file) | f-strings or `string.Template` | ~3MB |

---

## Frontend (14 runtime + 9 dev deps, 567 transitive)

### ESSENTIAL

| Dependency | Size | Used By | Verdict |
|------------|------|---------|---------|
| `react` / `react-dom` | Core | Entire app | Essential |
| `@tanstack/react-query` | 500KB | All data fetching | Essential |
| `lucide-react` | 2MB | All icons | Essential |
| `radix-ui` | 1MB | Primitives (popover, dialog, tooltip) | Essential |
| `class-variance-authority` / `clsx` / `tailwind-merge` | <100KB | shadcn/ui utilities | Essential |
| `react-resizable-panels` | 200KB | Split pane layout | Essential |
| `remark-gfm` | 500KB | Markdown tables, task lists | Essential (with react-markdown) |

### HEAVY — Worth Evaluating

| Dependency | Installed Size | Used By | Recommendation |
|------------|---------------|---------|----------------|
| `react-syntax-highlighter` | **8.7 MB** (installed) | Code blocks in chat | **KEEP** — already optimized: uses PrismLight with 25 registered languages (~200KB lazy chunk), not full Prism (~1.6MB). npm install size is large but runtime bundle is small via tree-shaking + Vite manual chunks. |
| `recharts` | **8.4 MB** | Dashboard charts (metrics pane) | **EVALUATE** — if only simple line/bar charts, `lightweight-charts` or `chart.js` are 5-10x smaller. But recharts has good React integration. |
| `react-markdown` | 2 MB | Chat message rendering | **KEEP** — core feature, well-optimized with manual chunking |
| `geist` | 1.5 MB | Font family | **KEEP** — branding font |

### REMOVABLE

| Dependency | Reason |
|------------|--------|
| (none in web) | Frontend deps are lean for a React app |

---

## Docker Images

### MCP Server: 4.09 GB — Breakdown

| Layer | Approx Size | Notes |
|-------|-------------|-------|
| python:3.11-slim base | 150 MB | Minimal |
| Build tools (gcc, build-essential) | 0 MB (multi-stage, not in final) | Good |
| Python packages | ~800 MB | onnxruntime, numpy, chromadb, etc. |
| Pre-downloaded ONNX models | ~500 MB | arctic-embed-m-v1.5 + ms-marco-MiniLM |
| tesseract-ocr | 2.7 MB | OCR (Pro-only, removable) |
| ffmpeg | 2.8 MB | Audio (Pro-only, removable) |
| HuggingFace cache | ~2.5 GB | Model files cached at build time |

**Recommendations:**
1. **Don't pre-download models in Dockerfile** — let them lazy-download on first use. Saves ~3 GB from image. Tradeoff: slower first request.
2. **Remove tesseract-ocr and ffmpeg** — Pro-only plugins. Install via sidecar or plugin script.
3. **Use python:3.11-alpine** — saves ~50 MB but may break some C extensions. Test carefully.

### Web Frontend: 98.7 MB — Already Optimal

nginx:alpine base + Vite build output. Nothing to optimize.

---

## Recommended Removal Plan (Priority Order)

### Tier 1: Zero-Risk Removals (no functionality loss)

| Action | Savings | Risk |
|--------|---------|------|
| Remove `packages/desktop/` from repo | Eliminates 26/33 Dependabot vulns | Zero — desktop app is unused |
| Remove `stripe` from requirements.txt | ~5 MB, 0 imports | Zero — unused, no imports found |
| Remove `faster-whisper` from requirements.txt | ~50 MB | Zero — unused, no imports found |
| Remove `requests` from requirements.txt | ~5 MB | Near-zero — only CLI scripts, can use httpx |

**Impact: Eliminates 26 vulnerabilities, saves ~60 MB**

### Tier 2: Low-Risk Removals (minor refactoring)

| Action | Savings | Effort |
|--------|---------|--------|
| Remove `structlog` — replace 1 import with `logging` | ~3 MB | 5 min |
| Move `pytesseract` + `Pillow` to plugin-level requirements | ~12 MB + 2.7 MB tesseract | 10 min |
| Move `bcrypt` + `PyJWT` to optional extras `[auth]` | ~3 MB | 15 min |
| Replace `jinja2` with f-strings in models.py | ~3 MB | 20 min |

**Impact: Saves ~22 MB, cleaner dependency tree**

### Tier 3: Medium-Effort Replacements (significant savings)

| Action | Savings | Effort |
|--------|---------|--------|
| ~~Replace `langgraph`~~ | PROTECTED — real routing graph | N/A |
| ~~Replace `pandas`~~ | PROTECTED — CSV enrichment pipeline | N/A |
| `react-syntax-highlighter` | **PROTECTED** — PrismLight with 25 languages, runtime ~200KB | N/A — keep | 0 |
| Stop pre-downloading ONNX models in Docker | ~3 GB image size | 30 min (add lazy-download logic) |

**Impact: Saves ~3 GB Docker image (model preload toggle)**

### Tier 4: Aggressive Optimization (evaluate later)

| Action | Savings | Risk |
|--------|---------|------|
| Replace `recharts` with lightweight-charts | ~7 MB | High — significant API change |
| Switch to python:3.11-alpine | ~50 MB | Medium — C extension compatibility |
| Make `sentry-sdk` optional install | ~10 MB | Low — already opt-in |

---

## Summary

| Category | Current | After Tier 1 | After Tier 1+2 | After All |
|----------|---------|-------------|----------------|-----------|
| Dependabot vulns | 33 | **7** | 7 | 7 |
| Python direct deps | 20 | 17 | 13 | 11 |
| Docker image (MCP) | 4.09 GB | 4.03 GB | 3.99 GB | **~1 GB** |
| Frontend bundle | ~2 MB | ~2 MB | ~2 MB | ~1.5 MB |
| npm transitive | 567 | ~400 | ~400 | ~400 |
| pip transitive | 156 | ~150 | ~145 | **~100** |
