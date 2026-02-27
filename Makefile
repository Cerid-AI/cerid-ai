.PHONY: lock-python lock-python-dev install-hooks deps-check

lock-python:
	cd src/mcp && pip-compile requirements.txt -o requirements.lock --generate-hashes --no-header --allow-unsafe

lock-python-dev:
	cd src/mcp && pip-compile requirements-dev.txt -o requirements-dev.lock --generate-hashes --no-header --allow-unsafe

lock-all: lock-python lock-python-dev

install-hooks:
	git config core.hooksPath scripts/hooks
	@echo "Git hooks installed from scripts/hooks/"

deps-check:
	cd src/mcp && pip-compile requirements.txt -o /tmp/req.lock --generate-hashes --no-header --allow-unsafe && diff requirements.lock /tmp/req.lock
	cd src/web && npm ci
