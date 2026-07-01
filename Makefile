.PHONY: lock-python lock-python-dev lock-all install-hooks deps-check version-file \
       lint-frontend test-frontend typecheck-frontend build-frontend check-all \
       test test-all test-eval ci-local drift-check prepush smoke slo help

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
	cd src/web && npx tsc -b

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

# -- Full local validation (run by the pre-push hook; mirrors PR+merge CI) --
# Validates locally so Action minutes are only spent at merge time. Excludes
# preservation/benchmark/integration/eval — all need a live stack (Neo4j/Chroma/Redis)
# and would hard-fail on a host without it. Mirrors CI's unit-test job, which also runs
# -m "not benchmark_slo and not integration" (ci.yml). Escape hatch: git push --no-verify
ci-local: ## Full local validation before push (backend + frontend + guard)
	@echo "[ci-local] backend · ruff"
	.venv/bin/ruff check src/mcp/
	@echo "[ci-local] backend · mypy"
	.venv/bin/mypy src/mcp/
	@echo "[ci-local] backend · import contracts"
	cd src/mcp && ../../.venv/bin/lint-imports
	@echo "[ci-local] backend · tests"
	PYTHONPATH=src/mcp .venv/bin/pytest src/mcp/tests/ --ignore=src/mcp/tests/eval \
	  -m "not benchmark_slo and not preservation and not integration" -x -q -p no:cacheprovider
	@echo "[ci-local] frontend · eslint + tsc + vitest"
	cd src/web && npx eslint . && npx tsc -b && npx vitest run
	@echo "[ci-local] secret detection (matches CI security job)"
	bash scripts/detect-secrets-scan.sh
	@echo "[ci-local] supply-chain guard"
	bash scripts/guard-no-ai-commits.sh
	@echo "[ci-local] ✓ all local checks passed"

drift-check: ## Generated-doc, manifest, and lint gates the remote `lint` job runs (NOT in ci-local)
	@echo "[drift] env-example"
	.venv/bin/python scripts/gen_env_example.py --check
	@echo "[drift] router-registry"
	.venv/bin/python scripts/gen_router_registry.py --check
	@echo "[drift] route-response-model"
	.venv/bin/python scripts/lint-route-response-model.py --check
	@echo "[drift] retrieval-import-boundary"
	.venv/bin/python scripts/lint-retrieval-import-boundary.py --check
	@echo "[drift] magic-numbers"
	.venv/bin/python scripts/lint-magic-numbers.py --check
	@echo "[drift] gates-parity"
	@test -f scripts/lint-gates-parity.py \
	  && .venv/bin/python scripts/lint-gates-parity.py --check \
	  || echo "  (internal-only gate — not present in this checkout, skipped)"
	@echo "[drift] model-name-uniqueness"
	.venv/bin/python scripts/lint-model-name-uniqueness.py --check
	@echo "[drift] sdk-openapi"
	.venv/bin/python scripts/gen_sdk_openapi.py --check
	@echo "[drift] sync-manifest"
	@test -f scripts/lint-sync-manifest.py \
	  && .venv/bin/python scripts/lint-sync-manifest.py \
	  || echo "  (internal-only gate — not present in this checkout, skipped)"
	@echo "[drift] public-leak-preflight"
	@test -f scripts/lint-public-leak-preflight.py \
	  && .venv/bin/python scripts/lint-public-leak-preflight.py \
	  || echo "  (internal-only gate — not present in this checkout, skipped)"
	@echo "[drift] silent-catch"
	.venv/bin/python scripts/lint-no-silent-catch.py src/mcp/
	@echo "[drift] no-legacy-neo4j-tree"
	.venv/bin/python scripts/lint-no-legacy-neo4j-tree.py
	@echo "[drift] import-star-without-all"
	.venv/bin/python scripts/lint-import-star-without-all.py
	@echo "[drift] no-module-getenv-mutable"
	.venv/bin/python scripts/lint-no-module-getenv-mutable.py
	@echo "[drift] docker-healthcheck-localhost"
	.venv/bin/python scripts/lint-docker-healthcheck-localhost.py
	@echo "[drift] web-no-crypto-randomuuid"
	.venv/bin/python scripts/lint-web-no-crypto-randomuuid.py
	@echo "[drift] dts-basename-collision"
	.venv/bin/python scripts/lint-dts-basename-collision.py
	@echo "[drift] product-story"
	.venv/bin/python scripts/lint-product-story.py
	@echo "[drift] mcp-descriptions"
	.venv/bin/python scripts/lint-mcp-descriptions.py
	@echo "[drift] no-hardcoded-models"
	.venv/bin/python scripts/lint-no-hardcoded-models.py --strict src/mcp/
	@echo "[drift] pro-gating"
	.venv/bin/python scripts/lint-pro-gating.py
	@echo "[drift] ✓ drift + lint gates passed"

prepush: ci-local drift-check ## FULL pre-push parity with remote CI (run before every push)
	@echo "[prepush] ✓ complete — safe to push"

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
	@cd src/mcp && ../../.venv/bin/python -m pytest tests/integration/ -m preservation -v --tb=short \
	  --junit-xml=/tmp/preservation-results.xml ; \
	rc=$$? ; \
	cd ../.. ; \
	python3 scripts/write-preservation-baseline.py \
	  --junit-xml /tmp/preservation-results.xml --source local >/dev/null 2>&1 || true ; \
	exit $$rc

# -- Latency SLO benchmarks --
slo: ## Run latency SLO benchmarks against localhost:8888 (requires running stack)
	cd src/mcp && ../../.venv/bin/python -m pytest tests/test_latency_slo.py -m benchmark_slo --benchmark-only -v

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
