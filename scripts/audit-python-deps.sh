#!/usr/bin/env bash
# pip-audit over the pinned lock and the installed Python dependency tree, with the curated ignore
# list. Mirrors the ci.yml `security` job step.
#
# Extracted 2026-08-04 so CI and `make prepush` share ONE list. It previously
# lived only in ci.yml, which meant a new advisory could not be discovered until
# after a push — and on 2026-08-03 three new cryptography advisories did exactly
# that, red-lighting main for eight consecutive runs.
#
#   --native   audit the lock, then the CURRENT environment (CI, which installs requirements.txt first)
#   (default)  the same two audits inside python:3.12-slim via Docker, mirroring CI
#
# Never audit the ambient local venv. A first version did, and immediately
# reported urllib3 2.6.3 (PYSEC-2026-141/142) against a tree whose lock pins the
# fixed 2.7.0 — a stale venv, not a repo defect. An environment-dependent
# security gate reports the developer's machine, and it can fail EITHER way: a
# venv NEWER than the lock stays silent on a vulnerability that actually ships.
# Docker makes the local result reproducible and equal to CI's.
#
# TWO TREES. The Dockerfile ships `pip install --require-hashes -r
# requirements.lock`, so the lock is audited first, by its pins alone:
# --disable-pip installs and resolves nothing, so the linux-only wheels in it
# (torch==2.13.0+cpu has no macOS wheel) do not matter and the answer does not depend
# on the host. The fresh latest-in-range resolution of requirements.txt is still
# audited after it, because that is what a source install and the next
# regen-lock.sh get. Auditing only the fresh tree let the shipped pins go unseen:
# on 2026-10-01 the image carried urllib3 2.7.0, pypdf 6.15.0 and oauthlib 3.3.1,
# each with a fixed advisory, while the fresh resolution had moved past all three.
#
# SUNSET POLICY: every ignore carries a re-evaluate date. When the date lands the
# entry must be removed and re-justified, not silently extended. An ignore
# without a live justification is an unreported vulnerability.
set -euo pipefail
cd "$(dirname "$0")/.."

# The venv candidate must actually RUN, not merely exist: inside a CI
# container the bind-mounted repo carries the host's .venv, whose interpreter
# path does not exist there — `-x` passes and the exec then dies.
PY="${PYTHON:-.venv/bin/python}"
{ [ -x "$PY" ] && "$PY" -c 1 >/dev/null 2>&1; } || PY="python3"
PYTHON_IMAGE="python:3.12-slim"
PIP_AUDIT_VERSION="2.10.0"

# CVE-2026-45829      Pre-auth code injection in chromadb via trust_remote_code=true. Never set
#                     anywhere (grep-verified); Chroma binds loopback. Re-eval 2026-11-30 (verified still firing 2026-08-31).
# CVE-2026-45830      Chroma performs no authorization validation, so ANY authenticated user can
# CVE-2026-45831      read/write/delete another tenant's collections; and SimpleRBACAuthorizationProvider
#                     checks that a permission is held but never which tenant/database/collection it
#                     applies to. Both presuppose a deployment with Chroma authn/authz turned on.
#                     Ours has none: no CHROMA_SERVER_AUTHN_*/AUTHZ_* is set anywhere
#                     (grep-verified), so no authenticated principal exists to escalate, and the
#                     server publishes on 127.0.0.1 only in both compose files with no gateway or
#                     tunnel route to it. Cerid's own tenant isolation does not go through Chroma's
#                     tenant primitives either — core/context/identity.py fuses tenant_id into the
#                     `where` clause at the application layer — so neither CVE governs it.
#                     RESIDUAL, accepted: if Chroma authn is ever enabled, or its native
#                     tenants/databases are ever adopted, both become live and this entry must be
#                     re-justified before that ships. No fixed version exists — 1.5.9 is still the
#                     newest release on PyPI (checked 2026-10-01; no CHROMA_SERVER_AUTHN/AUTHZ
#                     set anywhere and Chroma still publishes on 127.0.0.1:8001 only).
#                                                                      Re-eval 2026-11-30 (chromadb release cadence, with CVE-2026-45829).
# CVE-2026-45833      Post-auth code injection in chromadb via a malicious model repository with
#                     trust_remote_code=true on collection update. Same mechanism as CVE-2026-45829
#                     above and the same basis: trust_remote_code is never set anywhere
#                     (grep-verified), and the UPDATE_COLLECTION permission it requires only exists
#                     under an authz provider we do not configure.     Re-eval 2026-11-30 (verified still firing 2026-08-31).
IGNORES=(
  CVE-2026-45829
  CVE-2026-45830
  CVE-2026-45831
  CVE-2026-45833
)

IGNORE_ARGS=""
for v in "${IGNORES[@]}"; do IGNORE_ARGS="${IGNORE_ARGS} --ignore-vuln ${v}"; done

# OSV, not pip-audit's default PyPI service. torch, torchaudio and torchcodec come
# from PyTorch's CPU index as local versions (2.13.0+cpu), which PyPI's JSON API
# 404s, so the PyPI service lists them under "Skip Reason" and exits 0: torch would
# drop out of the audit with nothing failing. OSV matches the version by PEP 440
# and reports the same advisories for everything else (compared 2026-10-01).
SERVICE_ARGS="--vulnerability-service osv"

# Both audits always run, so a lock finding does not hide a fresh-tree one.
if [ "${1:-}" = "--native" ]; then
  rc=0
  echo "== pinned src/mcp/requirements.lock (what the image ships)"
  # shellcheck disable=SC2086
  "$PY" -m pip_audit --desc -r src/mcp/requirements.lock --require-hashes --disable-pip ${SERVICE_ARGS} ${IGNORE_ARGS} || rc=1
  echo "== this environment (fresh resolution of requirements.txt)"
  # shellcheck disable=SC2086
  "$PY" -m pip_audit --desc ${SERVICE_ARGS} ${IGNORE_ARGS} || rc=1
  exit "$rc"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required to audit deterministically (the lock is" >&2
  echo "       linux-resolved). Pass --native if you are already on linux." >&2
  exit 1
fi

echo "Auditing dependencies in ${PYTHON_IMAGE}..."
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work "${PYTHON_IMAGE}" \
  sh -c "pip install --quiet --upgrade 'pip>=26.0' && \
         pip install --quiet pip-audit==${PIP_AUDIT_VERSION} || exit 1; rc=0; \
         echo '== pinned requirements.lock (what the image ships)'; \
         pip-audit --desc -r requirements.lock --require-hashes --disable-pip ${SERVICE_ARGS} ${IGNORE_ARGS} || rc=1; \
         echo '== fresh resolution of requirements.txt'; \
         { pip install --quiet -r requirements.txt && \
           pip install --quiet --upgrade 'setuptools>=83.0.0' && \
           pip-audit --desc ${SERVICE_ARGS} ${IGNORE_ARGS}; } || rc=1; \
         exit \$rc"
# The image's own pip (25.0.1) trips PYSEC-2026-1795/1796 (tar/wheel extraction
# outside the install dir). Upgraded past the fix rather than added to IGNORES,
# following the setuptools precedent above: this is the installer tooling, not
# anything that ships. CI's hosted runner already carries a newer pip, which is
# why this only shows up in the container path.
