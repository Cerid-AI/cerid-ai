.PHONY: lock-python lock-python-dev lock-all install-hooks deps-check \
       lint-frontend test-frontend typecheck-frontend build-frontend check-all \
       test test-all test-eval help

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
	cd src/mcp && python -m pytest tests/ --ignore=tests/eval -x -q

test-all:
	cd src/mcp && python -m pytest tests/ -x -q

test-eval:
	cd src/mcp && python -m pytest tests/eval/ -v --tb=short

# -- Combined --
check-all: deps-check lint-frontend typecheck-frontend test-frontend

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
