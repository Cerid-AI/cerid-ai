#!/usr/bin/env bash
# Canonical detect-secrets scan — single source of truth for BOTH the CI
# `security` job and local `make ci-local`, so the local gate matches CI and a
# mock-secret in a test can't slip past prepush only to fail in CI.
#
# Scans git-tracked files (not --all-files, which walks .git/). Exits 1 if any
# secret is detected. Exclusions mirror real false-positive sources (test fakes,
# locks, the age vault, etc.); prefer an inline `# pragma: allowlist secret` on a
# specific line over adding a whole file here.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

# Resolve detect-secrets: dev venv → PATH (CI pip-installs it) → auto-install into
# the dev venv as a fallback (so `make ci-local` works on a fresh checkout without
# depending on requirements-dev.lock freshness).
#
# The venv candidate must actually RUN, not merely exist: inside the CI
# container the bind-mounted repo carries the host's .venv, whose shebang
# points at a macOS python that does not exist in the container — `-x` passes
# and the exec then dies with "No such file or directory".
if [ -x .venv/bin/detect-secrets ] && .venv/bin/detect-secrets --version >/dev/null 2>&1; then
  DS=.venv/bin/detect-secrets
elif command -v detect-secrets >/dev/null 2>&1; then
  DS=detect-secrets
elif [ -x .venv/bin/pip ]; then
  echo "[detect-secrets] not found — installing detect-secrets==1.5.0 into .venv …"
  .venv/bin/pip install -q detect-secrets==1.5.0 && DS=.venv/bin/detect-secrets
else
  echo "[detect-secrets] not installed and no .venv — run: pip install detect-secrets==1.5.0"
  exit 1
fi

TMPFILE=$(mktemp)
export TMPFILE
git ls-files -z | xargs -0 "$DS" scan \
  --exclude-files '\.env\.age$' \
  --exclude-files '\.lock$' \
  --exclude-files 'package-lock\.json$' \
  --exclude-files '\.github/workflows/' \
  --exclude-files 'src/mcp/docs/inventory/' \
  --exclude-files 'scripts/env-lock\.sh$' \
  --exclude-files 'docs/CERID_AI_PROJECT_REFERENCE\.md$' \
  --exclude-files 'docs/OPERATIONS\.md$' \
  --exclude-files 'src/mcp/tests/test_middleware_auth\.py$' \
  --exclude-files 'src/mcp/tests/test_auth\.py$' \
  --exclude-files 'src/mcp/tests/test_web_search\.py$' \
  --exclude-files 'docs/plans/' \
  --exclude-files 'docs/superpowers/plans/' \
  --exclude-files 'src/mcp/routers/setup\.py$' \
  --exclude-files 'src/mcp/app/routers/setup\.py$' \
  --exclude-files 'src/mcp/config/knowledge_packs\.json$' \
  > "$TMPFILE"

python3 -c "
import json, sys, os
with open(os.environ['TMPFILE']) as f:
    results = json.load(f)
secrets = {k: v for k, v in results.get('results', {}).items() if v}
if secrets:
    print('::error::Potential secrets detected in:')
    for fname, findings in secrets.items():
        for finding in findings:
            print(f'  {fname}:{finding[\"line_number\"]} - {finding[\"type\"]}')
    sys.exit(1)
print('No secrets detected.')
"
rc=$?
rm -f "$TMPFILE"
exit $rc
