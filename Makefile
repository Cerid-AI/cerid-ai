.PHONY: lock-python lock-python-dev lock-all install-hooks install-macos-integration \
       deps-check version-file \
       lint-frontend test-frontend typecheck-frontend build-frontend check-all \
       test test-all test-eval eval-live-retrieval eval-chat-faithfulness \
       eval-verdict bench-nli-aggrefact \
       ci-local drift-check prepush smoke slo help \
       security-local sdk-contract-local lock-check license-local frontend-full

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

# -- macOS integration --
install-macos-integration: ## Install Finder Quick Actions + Services menu (macOS; requires a running stack)
	bash scripts/install-macos-integration.sh

push: ## Validate FIRST, then push (avoids the hook holding the remote connection open)
	@bash scripts/safe-push.sh $(ARGS)

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

# -- Live eval harnesses (Quality-Maximization Phase 0.1 / 0.3) --
# Score LIVE retrieval + chat faithfulness against a running stack
# (MCP_BASE default http://localhost:8888). Need CERID_API_KEY in env or .env;
# the chat harness also needs OPENROUTER_API_KEY (env or .env). Both self-seed a
# deterministic eval-fixture corpus and tear it down after. Report-only unless
# RETRIEVAL_EVAL_MIN_RECALL5 / CHAT_FAITHFULNESS_MIN are set.
eval-live-retrieval: ## Live-retrieval golden-query eval (requires running stack)
	@echo "[eval-live-retrieval] requires stack (scripts/start-cerid.sh) + CERID_API_KEY"
	cd src/mcp && ../../.venv/bin/python -m tests.eval.live_retrieval_eval

eval-chat-faithfulness: ## Chat-path faithfulness eval (requires running stack; use --max-items for cost)
	@echo "[eval-chat-faithfulness] requires stack + CERID_API_KEY + OPENROUTER_API_KEY"
	cd src/mcp && ../../.venv/bin/python -m tests.eval.chat_faithfulness_eval

eval-verdict: ## Claim-verdict accuracy eval vs labeled cases (requires running stack)
	@echo "[eval-verdict] requires stack + CERID_API_KEY (report-only unless VERDICT_EVAL_MIN_ACCURACY set)"
	cd src/mcp && ../../.venv/bin/python -m tests.eval.verification_verdict_eval

bench-nli-aggrefact: ## Benchmark the local NLI gate on LLM-AggreFact (needs HF access)
	PYTHONPATH=src/mcp .venv/bin/python scripts/bench_nli_aggrefact.py

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
	@echo "[ci-local] ReDoS regex audit (matches CI security / dlint)"
	.venv/bin/python -m flake8 --select=DUO138 src/mcp/
	@echo "[ci-local] gate probes (scripts/tests)"
	.venv/bin/pytest scripts/tests/ -q -p no:cacheprovider
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
	@if [ -f docs/ROUTER_REGISTRY.md ]; then \
	  .venv/bin/python scripts/gen_router_registry.py --check; \
	else \
	  echo "  (internal-only doc — not present in this checkout, skipped)"; \
	fi
	@echo "[drift] route-response-model"
	.venv/bin/python scripts/lint-route-response-model.py --check
	@echo "[drift] retrieval-import-boundary"
	.venv/bin/python scripts/lint-retrieval-import-boundary.py --check
	@echo "[drift] version-consistency"
	.venv/bin/python scripts/lint-version-consistency.py --check
	@echo "[drift] magic-numbers"
	.venv/bin/python scripts/lint-magic-numbers.py --check
	@echo "[drift] test-antipatterns"
	.venv/bin/python scripts/lint-test-antipatterns.py --check
	@echo "[drift] ci-compose-namespacing"
	.venv/bin/python scripts/lint-ci-compose-namespacing.py
	@echo "[drift] external-fetch-boundary"
	.venv/bin/python scripts/lint-external-fetch-boundary.py --check
	@echo "[drift] gates-parity"
	@if [ -f scripts/lint-gates-parity.py ]; then \
	  .venv/bin/python scripts/lint-gates-parity.py --check; \
	else \
	  echo "  (internal-only gate — not present in this checkout, skipped)"; \
	fi
	@echo "[drift] model-name-uniqueness"
	.venv/bin/python scripts/lint-model-name-uniqueness.py --check
	@echo "[drift] sdk-openapi"
	.venv/bin/python scripts/gen_sdk_openapi.py --check
	@echo "[drift] sync-manifest"
	@if [ -f scripts/lint-sync-manifest.py ]; then \
	  .venv/bin/python scripts/lint-sync-manifest.py; \
	else \
	  echo "  (internal-only gate — not present in this checkout, skipped)"; \
	fi
	@echo "[drift] doc-env-vars"
	.venv/bin/python scripts/lint-doc-env-vars.py
	@echo "[drift] swift-helper-manifests"
	@if [ -f scripts/lint-swift-helper-manifests.py ]; then \
	  .venv/bin/python scripts/lint-swift-helper-manifests.py; \
	else \
	  echo "  (internal-only gate — not present in this checkout, skipped)"; \
	fi
	@echo "[drift] public-leak-preflight"
	@if [ -f scripts/lint-public-leak-preflight.py ]; then \
	  .venv/bin/python scripts/lint-public-leak-preflight.py; \
	else \
	  echo "  (internal-only gate — not present in this checkout, skipped)"; \
	fi
	@echo "[drift] silent-catch"
	.venv/bin/python scripts/lint-no-silent-catch.py --strict-broad src/mcp/
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
	@echo "[drift] web-reachability"
	@if [ -f scripts/web_reachability_allowlist.txt ]; then \
	  .venv/bin/python scripts/lint-web-reachability.py --check; \
	else \
	  echo "  (internal-only allowlist — not present in this checkout, skipped)"; \
	fi
	@echo "[drift] env-has-reader"
	.venv/bin/python scripts/lint-env-has-reader.py --check
	@echo "[drift] success-status-literal"
	.venv/bin/python scripts/lint-success-status-literal.py --check
	@echo "[drift] key-contract"
	.venv/bin/python scripts/lint-key-contract.py --check
	@echo "[drift] route-has-client"
	@if [ -f scripts/route_has_client_allowlist.txt ]; then \
	  .venv/bin/python scripts/lint-route-has-client.py --check; \
	else \
	  echo "  (internal-only allowlist — not present in this checkout, skipped)"; \
	fi
	@echo "[drift] real-fixture"
	@if [ -f scripts/lint-real-fixture.py ]; then \
	  .venv/bin/python scripts/lint-real-fixture.py --check; \
	else echo "  (skipped — internal-only gate absent)"; fi
	@echo "[drift] host-capability"
	.venv/bin/python scripts/lint-host-capability.py --check
	@echo "[drift] mcp-descriptions"
	.venv/bin/python scripts/lint-mcp-descriptions.py
	@echo "[drift] no-hardcoded-models"
	.venv/bin/python scripts/lint-no-hardcoded-models.py --strict src/mcp/
	@echo "[drift] pro-gating"
	.venv/bin/python scripts/lint-pro-gating.py
	@echo "[drift] license-headers"
	.venv/bin/python scripts/lint-license-headers.py
	@echo "[drift] design-drift (matches CI lint / no-design-drift)"
	.venv/bin/python scripts/lint-no-design-drift.py --root src/web/src --allow-file scripts/design_drift_allowlist.txt
	@echo "[drift] ci-required-gates"
	.venv/bin/python scripts/lint-ci-required-gates.py --workflow .github/workflows/ci.yml
	@echo "[drift] ✓ drift + lint gates passed"

security-local: ## The remote `security` job, minus nothing (detect-secrets + bandit + pip-audit + dlint)
	@echo "[security] secret detection"
	bash scripts/detect-secrets-scan.sh
	@echo "[security] bandit"
	.venv/bin/python -m bandit -r src/mcp/ -ll --skip B101,B615 -x src/mcp/tests
	@echo "[security] ReDoS regex audit (dlint)"
	.venv/bin/python -m flake8 --select=DUO138 src/mcp/
	@echo "[security] dependency audit (pip-audit, incl. transitive)"
	bash scripts/audit-python-deps.sh

# PYTHONPATH rather than `pip install -e` (which is what CI does): a gate must
# not mutate the developer's venv as a side effect, and it must not depend on
# whether someone happened to install the SDK earlier. Internal passed this
# target while public failed for exactly that reason — same class as the
# ambient-venv problem in scripts/audit-python-deps.sh.
sdk-contract-local: ## The remote `sdk-contract` job (Python + TypeScript contract tests)
	@echo "[sdk] python contract tests"
	PYTHONPATH=packages/sdk/python/src .venv/bin/python -m pytest \
	  packages/sdk/python/tests/ -q -p no:cacheprovider
	@echo "[sdk] typescript contract tests"
	# `npm ci` first, as CI does: without it the target passes or fails on
	# whether node_modules happens to be present (public had none, and tsc
	# reported a missing 'node' type definition rather than the real cause).
	cd packages/sdk/typescript && npm ci --no-audit --no-fund \
	  && npm run typecheck && npm test

license-local: ## The remote `license-scan` job's python half (~2s; lock-resolved via PyPI)
	@echo "[license-local] python license scan (lock-resolved)"
	@PATH="$(CURDIR)/.venv/bin:$$PATH" bash scripts/ci/license-scan-python.sh
# The npm half (license-scan-node-collect/check) runs `npm ci` across four
# package roots — minutes, not seconds — so it stays remote-only like docker.
# GATE-07 closed 2026-08-07: license-scan had been NEITHER mirrored here NOR
# named in the exempt list, while the header above claimed full parity.

lock-check: ## The remote `lock-sync` job — lock freshness vs requirements.txt
	@echo "[lock] requirements.lock freshness"
	bash scripts/check-lock-fresh.sh

frontend-full: ## The remote `frontend` + `frontend-desktop` jobs (build, bundle caps, audits)
	@echo "[frontend] vite build"
	cd src/web && npx vite build
	@echo "[frontend] bundle size caps"
	bash scripts/check-bundle-size.sh
	@echo "[frontend] npm audit (production — blocking)"
	cd src/web && npm audit --omit=dev --audit-level=high
	@echo "[frontend] npm audit (dev toolchain — advisory)"
	-cd src/web && npm audit --audit-level=high
	@echo "[frontend] desktop typecheck + unit tests"
	# Two bugs lived on this line, both vacuous-gate shaped:
	#   1. it guarded on src/desktop, a path that has never existed, so it
	#      reported "(skipped)" and checked nothing;
	#   2. `test -d X && (...) || echo skipped` ALSO swallows a failure INSIDE
	#      the parens — a broken npm ci printed "skipped" and exited 0.
	# An if/else keeps the two cases distinct: absent → skip, present → the
	# commands' own status propagates.
	# --ignore-scripts: better-sqlite3's node-gyp rebuild fails against the
	# host's node 25 and is irrelevant to tsc, which needs type definitions,
	# not compiled natives. CI builds it because it also packages the app.
	@if [ -d packages/desktop ]; then \
	  cd packages/desktop && npm ci --no-audit --no-fund --ignore-scripts && npm run typecheck && npm test; \
	else \
	  echo "  (packages/desktop absent — internal-only mirror, skipped)"; \
	fi

# FULL pre-push parity with remote CI. Every ci.yml job that can run without
# Docker or a live stack is chained here; `scripts/lint-gates-parity.py`
# enforces that claim against ci.yml rather than leaving it to memory, so a new
# CI step must be mirrored here or declared exempt in gates.yaml.
#
# DELIBERATELY NOT HERE — these need a live stack, a Docker daemon, or
# minutes of npm ci, and have their own targets:
#   preservation / lint-no-silent-preservation-skips → make preservation-check
#   benchmark-slo                                    → make slo
#   docker (hadolint + image build + Trivy)          → merge-time only
#   license-scan's npm half (4x npm ci)              → merge-time only
#   frontend-a11y (axe sweep rides frontend-full's vitest run)
prepush: ci-local drift-check security-local sdk-contract-local lock-check license-local frontend-full ## FULL pre-push parity with remote CI (run before every push)
	@echo "[prepush] ✓ complete — safe to push"

mutation-check: ## Do the tests DETECT faults? (injects real defect classes; survivors = blind spots)
	.venv/bin/python scripts/mutation_check.py

# -- Load testing --
smoke:
	@echo "[smoke] requires stack running (scripts/start-cerid.sh)"
	python3 src/mcp/tests/load/smoke.py

# -- Preservation harness --
# Gates every sprint in the consolidation program. Runs against the
# live stack at http://127.0.0.1:8888 (override with
# CERID_PRESERVATION_MCP). NEO4J_PASSWORD must be in the env or in .env.
pro-feature-health: ## Gate: no Pro feature is entitled-but-not-loaded (needs a live stack)
	@echo "[pro-feature-health] requires stack running (scripts/start-cerid.sh)"
	.venv/bin/python scripts/lint-pro-feature-health.py

validate-pro: pro-feature-health ## Full Pro-feature validation matrix against a live stack
	@echo "[validate-pro] Pro preservation + E2E suites"
	@cd src/mcp && ../../.venv/bin/python -m pytest \
	  tests/integration/test_preservation_apple_connectors.py \
	  tests/integration/test_preservation_cloud_connectors.py \
	  tests/integration/test_preservation_daily_digest.py \
	  tests/integration/test_preservation_inbox_triage.py \
	  tests/integration/test_preservation_meeting_capture.py \
	  tests/integration/test_apple_connectors_e2e.py \
	  tests/integration/test_meeting_capture_e2e.py \
	  -v --tb=short --junit-xml=/tmp/validate-pro-results.xml ; \
	rc=$$? ; \
	echo "" ; \
	echo "[validate-pro] skipped features (each needs a credential or host capability):" ; \
	$(CURDIR)/.venv/bin/python $(CURDIR)/scripts/lint-no-silent-preservation-skips.py \
	  --junit-xml /tmp/validate-pro-results.xml ; \
	skiprc=$$? ; \
	if [ $$skiprc -ne 0 ] ; then \
	  echo "[validate-pro] skip report FAILED (exit $$skiprc)" ; \
	  [ $$rc -eq 0 ] && rc=$$skiprc ; \
	fi ; \
	exit $$rc

preservation-check: ## Run capability-preservation invariants (I1-I8) against a live stack
	@echo "[preservation] requires stack running (scripts/start-cerid.sh)"
	@cd src/mcp && ../../.venv/bin/python -m pytest tests/integration/ -m preservation -v --tb=short \
	  --ignore-glob='tests/integration/test_processor_chaos.py' \
	  --ignore-glob='tests/integration/test_processor_end_to_end.py' \
	  --ignore-glob='tests/integration/test_o1_ingest_atomicity_preservation.py' \
	  --ignore-glob='tests/integration/test_o2_memory_consolidation_preservation.py' \
	  --ignore-glob='tests/integration/test_r3_hype_eval_gate.py' \
	  --ignore-glob='tests/integration/test_w4_contradiction_preservation.py' \
	  --ignore-glob='tests/integration/test_cl12_store_divergence_preservation.py' \
	  --ignore-glob='tests/integration/test_e1_*.py' \
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
	@echo "  install-macos-integration  Install Finder Quick Actions + Services menu (macOS)"
	@echo "  deps-check         Verify lock files and npm deps are current"
	@echo "  lint-frontend      Run ESLint on src/web/"
	@echo "  test-frontend      Run Vitest on src/web/"
	@echo "  typecheck-frontend Run TypeScript type check on src/web/"
	@echo "  build-frontend     Build production bundle"
	@echo "  test               Run unit + integration tests (skip eval)"
	@echo "  test-all           Run ALL tests including eval"
	@echo "  test-eval          Run evaluation harness only (Monte Carlo, RAGAS)"
	@echo "  eval-live-retrieval    Live-retrieval golden-query eval (running stack)"
	@echo "  eval-chat-faithfulness Chat-path faithfulness eval (running stack)"
	@echo "  check-all          Run deps-check + lint + typecheck + test"
	@echo "  smoke              Run smoke/load harness (requires running stack)"
	@echo "  slo                Run latency SLO benchmarks (requires running stack)"
