#!/usr/bin/env bash
# pip-audit over the installed Python dependency tree, with the curated ignore
# list. Mirrors the ci.yml `security` job step.
#
# Extracted 2026-08-04 so CI and `make prepush` share ONE list. It previously
# lived only in ci.yml, which meant a new advisory could not be discovered until
# after a push — and on 2026-08-03 three new cryptography advisories did exactly
# that, red-lighting main for eight consecutive runs.
#
#   --native   audit the CURRENT environment (CI, which installs requirements.txt first)
#   (default)  resolve + audit inside python:3.12-slim via Docker, mirroring CI
#
# Never audit the ambient local venv. A first version did, and immediately
# reported urllib3 2.6.3 (PYSEC-2026-141/142) against a tree whose lock pins the
# fixed 2.7.0 — a stale venv, not a repo defect. An environment-dependent
# security gate reports the developer's machine, and it can fail EITHER way: a
# venv NEWER than the lock stays silent on a vulnerability that actually ships.
# Docker makes the local result reproducible and equal to CI's.
#
# KNOWN GAP (deliberate, not fixed here): this audits a fresh latest-in-range
# resolution of requirements.txt, which is what CI does — but the Dockerfile
# ships `pip install --require-hashes -r requirements.lock`. Those can differ.
# Auditing the lock directly is the better question to ask, but pip-audit -r
# performs a dry-run install and the lock is linux-resolved (cuda-bindings has no
# macOS wheel), so it needs the container path plus a decision about changing the
# CI contract. Today the two agree.
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

# CVE-2026-26013      SSRF in ChatOpenAI image token counting — we use the Bifrost proxy, not
#                     ChatOpenAI.                                      Re-eval 2026-06-30 (Phase 11 langchain-core 0.3→1.2).
# CVE-2025-64439      RCE in LangGraph JsonPlusSerializer — we control all inputs, no untrusted
#                     data.                                            Re-eval 2026-06-30 (Phase 11).
# CVE-2026-27794      RCE via pickle fallback — internal caching only, no untrusted pickle read.
#                                                                      Re-eval 2026-06-30 (Phase 11).
# CVE-2026-28277      msgpack deserialization in the LangGraph checkpointer — we don't persist
#                     checkpointers.                                   Re-eval 2026-06-30 (Phase 11).
# CVE-2026-4539       ReDoS in the pygments AdlLexer — local-only, we don't use that lexer.
#                                                                      Re-eval 2026-07-31 (pygments cadence).
# CVE-2026-3219       pip concatenated tar+ZIP confusion — no upstream fix; we install only from
#                     pinned hashes in requirements.lock, so no poly-glot archive reaches the
#                     install path. CI runner's preinstalled pip only. Re-eval 2026-09-30 (pip cadence).
# GHSA-fv5p-p927-qmxr SSRF via redirect bypass in langchain-text-splitters
#                     HTMLHeaderTextSplitter.split_text_from_url(). We only call
#                     MarkdownHeaderTextSplitter.split_text(text) — no URL fetching, no HTML
#                     splitter anywhere.                               Re-eval 2026-06-30 (Phase 11).
# CVE-2026-6357       pip self-update ran after installing wheels. Production installs from pinned
#                     hashes with self-update disabled; CI runner pip only.
#                                                                      Re-eval 2026-08-31 (pip 26.1 on hosted runners).
# PYSEC-2025-183      pyjwt weak-encryption — disputed upstream (WONTFIX). Requires the consumer to
#                     pass a short HMAC key; ours is the operator-supplied CERID_JWT_SECRET, and the
#                     JWT path is gated behind CERID_MULTI_USER=true.  Re-eval 2026-08-31.
# CVE-2026-45829      Pre-auth code injection in chromadb via trust_remote_code=true. Never set
#                     anywhere (grep-verified); Chroma binds loopback. Re-eval 2026-09-30.
# PYSEC-2026-196      pip writes console_scripts outside the resolved install dir. CI runner pip
#                     only; we author no malicious entry-point names.  Re-eval 2026-08-31 (pip 26.1.2).
# CVE-2025-3000       torch.jit.script memory corruption, local-host vector only. Never called
#                     (grep-verified); no fixed version published.     Re-eval 2026-07-31 (torch cadence).
# PYSEC-2026-3624     lightning RCE via attacker-crafted checkpoint in load_from_checkpoint.
#                     Our only path into lightning's checkpoint loader is pyannote's
#                     pyannote/speaker-diarization-3.1, pulled from HF with the operator's token
#                     (plugins/meeting_capture/diarize.py), on an operator-gated optional plugin.
#                     The model id is not user-supplied and is now PINNED TO AN IMMUTABLE
#                     REVISION (84fd259, 2026-08-08) — previously it was a bare name, which
#                     resolves to whatever upstream's default branch points at, so this
#                     justification asserted an immutability the code did not enforce.
#                     tests/test_meeting_capture_diarize_pin.py fails if the pin is removed.
#                     RESIDUAL, accepted: the pinned config names sub-models (segmentation-3.0)
#                     that pyannote resolves at their own floating revisions; a compromise of
#                     THOSE repos is not covered. Fixed only in an unreleased commit — 2.6.5 is
#                     still the newest release on PyPI (checked 2026-08-08), so there is no
#                     version to upgrade to.                           Re-eval 2026-09-30 (lightning release cadence).
IGNORES=(
  CVE-2026-26013
  CVE-2025-64439
  CVE-2026-27794
  CVE-2026-28277
  CVE-2026-4539
  CVE-2026-3219
  GHSA-fv5p-p927-qmxr
  CVE-2026-6357
  PYSEC-2025-183
  CVE-2026-45829
  PYSEC-2026-196
  CVE-2025-3000
  PYSEC-2026-3624
)

IGNORE_ARGS=""
for v in "${IGNORES[@]}"; do IGNORE_ARGS="${IGNORE_ARGS} --ignore-vuln ${v}"; done

if [ "${1:-}" = "--native" ]; then
  # shellcheck disable=SC2086
  exec "$PY" -m pip_audit --desc ${IGNORE_ARGS}
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required to audit deterministically (the lock is" >&2
  echo "       linux-resolved). Pass --native if you are already on linux." >&2
  exit 1
fi

echo "Auditing dependencies in ${PYTHON_IMAGE}..."
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work "${PYTHON_IMAGE}" \
  sh -c "pip install --quiet --upgrade 'pip>=26.0' && \
         pip install --quiet pip-audit==${PIP_AUDIT_VERSION} && \
         pip install --quiet -r requirements.txt && \
         pip install --quiet --upgrade 'setuptools>=83.0.0' && \
         pip-audit --desc ${IGNORE_ARGS}"
# The image's own pip (25.0.1) trips PYSEC-2026-1795/1796 (tar/wheel extraction
# outside the install dir). Upgraded past the fix rather than added to IGNORES,
# following the setuptools precedent above: this is the installer tooling, not
# anything that ships. CI's hosted runner already carries a newer pip, which is
# why this only shows up in the container path.
