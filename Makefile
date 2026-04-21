.PHONY: lock-python lock-python-dev lock-all install-hooks deps-check version-file \
       lint-frontend test-frontend typecheck-frontend build-frontend check-all \
       test test-all test-eval smoke slo help

# -- Python deps --
lock-python:
	cd src/mcp && pip-compile requirements.txt -o requirements.lock --generate-hashes --no-header --allow-unsafe

lock-python-dev:
	cd src/mcp && pip-compile requirements-dev.txt -o requirements-dev.lock --generate-hashes --no-header --allow-unsafe

lock-all: lock-python lock-python-dev

# -- Git hooks --
install-hooks:
	git config core.hooksPath scripts/hooks
	@echo "Git hooks installed from scripts/hooks/"

# -- Build artifacts --
version-file:
	@python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])" > src/mcp/VERSION
	@echo "[version-file] wrote $$(cat src/mcp/VERSION)"

# -- Validation --
deps-check:
	cd src/mcp && pip-compile requirements.txt -o /tmp/req.lock --generate-hashes --no-header --allow-unsafe && diff requirements.lock /tmp/req.lock
	cd src/web && npm ci

# -- Frontend --
lint-frontend:
	cd src/web && npx eslint .

test-frontend:
	cd src/web && npx vitest run

typecheck-frontend:
	cd src/web && npx tsc --noEmit

build-frontend:
	cd src/web && npm run build

# -- Python tests (tiered) --
test:
	cd src/mcp && python -m pytest tests/ --ignore=tests/eval -m "not benchmark_slo" -x -q

test-all:
	cd src/mcp && python -m pytest tests/ -x -q

test-eval:
	cd src/mcp && python -m pytest tests/eval/ -v --tb=short

# -- Combined --
check-all: deps-check lint-frontend typecheck-frontend test-frontend

# -- Load testing --
smoke:
	@echo "[smoke] requires stack running (scripts/start-cerid.sh)"
	python3 src/mcp/tests/load/smoke.py

# -- Preservation harness --
# Gates every sprint in the consolidation program. Runs against the
# live stack at http://127.0.0.1:8888 (override with
# CERID_PRESERVATION_MCP). NEO4J_PASSWORD must be in the env or in .env.
preservation-check: ## Run capability-preservation invariants (I1-I8) against a live stack
	@echo "[preservation] requires stack running (scripts/start-cerid.sh)"
	@test -n "$$NEO4J_PASSWORD" || set -a && . .env && set +a; \
	docker exec ai-companion-mcp python -m pytest tests/integration/ -m preservation -v --tb=short

# -- Latency SLO benchmarks --
slo: ## Run latency SLO benchmarks against localhost:8888 (requires running stack)
	cd src/mcp && pytest tests/test_latency_slo.py -m benchmark_slo --benchmark-only -v

help:
	@echo "Available targets:"
	@echo "  lock-python        Regenerate requirements.lock"
	@echo "  lock-python-dev    Regenerate requirements-dev.lock"
	@echo "  lock-all           Regenerate both lock files"
	@echo "  install-hooks      Install git hooks from scripts/hooks/"
	@echo "  deps-check         Verify lock files and npm deps are current"
	@echo "  lint-frontend      Run ESLint on src/web/"
	@echo "  test-frontend      Run Vitest on src/web/"
	@echo "  typecheck-frontend Run TypeScript type check on src/web/"
	@echo "  build-frontend     Build production bundle"
	@echo "  test               Run unit + integration tests (skip eval)"
	@echo "  test-all           Run ALL tests including eval"
	@echo "  test-eval          Run evaluation harness only (Monte Carlo, RAGAS)"
	@echo "  check-all          Run deps-check + lint + typecheck + test"
	@echo "  smoke              Run smoke/load harness (requires running stack)"
	@echo "  slo                Run latency SLO benchmarks (requires running stack)"
