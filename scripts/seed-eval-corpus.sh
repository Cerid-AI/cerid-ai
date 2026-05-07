#!/usr/bin/env bash
# Workstream E Phase 1.2.b — eval corpus seeder (host-side wrapper).
#
# Thin wrapper around scripts/seed_eval_corpus.py that runs the Python
# seeder INSIDE the MCP container. The Python path is canonical because
# it bypasses /ingest_file's archive-path guard (validate_file_path
# restricts inputs to ${ARCHIVE_PATH}=/archive). Calling
# services.ingestion.ingest_content directly with metadata is the clean
# way to seed the eval corpus without polluting the user's archive
# folder or relaxing the security validator.
#
# Idempotent — cerid dedupes by content_hash, so re-running on an
# already-seeded corpus reports each duplicate as status=duplicate
# (treated as success).
#
# Usage:
#   scripts/seed-eval-corpus.sh                       # default localhost:8888
#   CERID_HOST=http://otherhost:8888 scripts/seed-eval-corpus.sh
#   CERID_CORPUS_VERSION=v2 scripts/seed-eval-corpus.sh
#
# After seeding, capture the IR baseline:
#   docker exec ai-companion-mcp bash -c \
#     'cd /app && PYTHONPATH=/app python -m tests.eval.test_retrieval_baselines'
set -euo pipefail

set -euo pipefail

CONTAINER="${CERID_CONTAINER:-ai-companion-mcp}"
CERID_CORPUS_VERSION="${CERID_CORPUS_VERSION:-v1}"

if ! docker exec "${CONTAINER}" true 2>/dev/null; then
  echo "error: container '${CONTAINER}' not running (try docker compose up -d)" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST_SCRIPT="${REPO_ROOT}/scripts/seed_eval_corpus.py"

if [[ ! -f "${HOST_SCRIPT}" ]]; then
  echo "error: missing ${HOST_SCRIPT}" >&2
  exit 1
fi

# Copy the python seeder into the container's writable /tmp and run it.
# The corpus itself is already mounted at /eval-corpus per docker-compose.yml.
docker cp "${HOST_SCRIPT}" "${CONTAINER}:/tmp/seed_eval_corpus.py"
docker exec \
  -e PYTHONPATH=/app \
  -e CERID_CORPUS_VERSION="${CERID_CORPUS_VERSION}" \
  "${CONTAINER}" python /tmp/seed_eval_corpus.py
