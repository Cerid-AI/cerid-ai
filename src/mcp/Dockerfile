# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

# Build context: repo root (NOT src/mcp/). The build needs pyproject.toml
# at the repo root to bake the canonical version into /app/VERSION.
# Build with: docker build -f src/mcp/Dockerfile .

# Stage 1: Builder — install Python deps with build tools
FROM python:3.12.13-slim-trixie AS builder

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends gcc build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools

COPY src/mcp/requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Stage 2: Models — pre-download ONNX models into HuggingFace cache
# Workstream E Phase E.6.5: default flipped to false (~3 GB lighter image).
# Set CERID_PRELOAD_MODELS=true to bake models in for offline / air-gapped
# deployments. Operators on the default lean image warm the cache via the
# Settings → Inference Models panel ("Download models" button) or by
# running `curl -X POST http://localhost:8888/setup/models/preload`.
# See docs/MODEL_PRELOAD.md for the trade-off and the GPU sidecar path.
FROM builder AS models

ARG CERID_PRELOAD_MODELS=false
# Always create the cache dir — the runtime stage's COPY --from=models
# requires a source path to exist, even when preload is skipped (lean
# image case). Without this, the COPY fails with "ENOENT" and the
# build aborts with no useful diagnostic.
RUN mkdir -p /root/.cache/huggingface && \
    if [ "$CERID_PRELOAD_MODELS" = "true" ]; then \
    python -c "\
from huggingface_hub import hf_hub_download; \
hf_hub_download('cross-encoder/ms-marco-MiniLM-L-6-v2', 'onnx/model.onnx'); \
hf_hub_download('cross-encoder/ms-marco-MiniLM-L-6-v2', 'onnx/model_quint8_avx2.onnx'); \
hf_hub_download('cross-encoder/ms-marco-MiniLM-L-6-v2', 'tokenizer.json'); \
hf_hub_download('Snowflake/snowflake-arctic-embed-m-v1.5', 'onnx/model.onnx'); \
hf_hub_download('Snowflake/snowflake-arctic-embed-m-v1.5', 'tokenizer.json')"; \
    else echo "Skipping model preload (CERID_PRELOAD_MODELS=false)"; fi

# Stage 3: Runtime — slim image without build tools
FROM python:3.12.13-slim-trixie

# Disable chromadb's client-side posthog telemetry. Chromadb reads these at
# module import time, before any Python code can override via Settings(...).
# Aligns with cerid's self-hosted privacy-first default.
ENV ANONYMIZED_TELEMETRY=false \
    CHROMA_ANONYMIZED_TELEMETRY=false

# ffmpeg is REQUIRED, not optional: the meeting_capture Pro plugin ships in
# this image and cannot run without it. decode.py shells out to the `ffmpeg`
# binary to make 16 kHz mono PCM, and pyannote 4.x decodes through torchcodec,
# which dlopen()s the FFmpeg shared libs. Neither existed here, so Meeting
# Capture was undeployable in the shipped image even once its plugin loaded —
# and decode.py's docstring claimed ffmpeg was "bundled with Cerid's Docker
# image", which it was not. The older "install it in a plugin-specific layer"
# note is dropped because no such layer was ever built.
# tesseract-ocr stays out: the ocr plugin degrades cleanly without it.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 cerid

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Drop pip from the RUNTIME image. Its vendored copies of msgpack and
# setuptools were the image's only remaining HIGH Trivy findings
# (GHSA-6v7p-g79w-8964, CVE-2025-47273), and there is no upstream fix to wait
# for: pip 26.1.2 — the newest release — vendors msgpack==1.1.2 and
# setuptools==70.3.0, and so does pip's main branch. Upgrading pip changes
# nothing.
#
# Removing it is the real fix rather than a suppression. Nothing here needs
# pip: CMD is uvicorn, no code shells out to it, and every "pip install ..."
# string in the tree is operator guidance in an error message. It also matches
# the plugin convention stated above — optional dependencies belong in a
# plugin-specific build layer or the sidecar, not installed into a running
# container. The real setuptools (81.0.0, from requirements.lock, above both
# fixed versions) is untouched and still provides pkg_resources.
#
# If a future build layer needs pip, install it in THAT stage; do not restore
# it here.
RUN python -m pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.12/site-packages/pip \
              /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
              /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12
# Copy preloaded models if they exist (no-op if CERID_PRELOAD_MODELS=false)
RUN mkdir -p /home/cerid/.cache/huggingface
COPY --from=models /root/.cache/huggingface/ /home/cerid/.cache/huggingface/
RUN chown -R cerid:cerid /home/cerid/.cache

WORKDIR /app
COPY --chown=cerid:cerid src/mcp/ /app/

# Bake the canonical version from pyproject.toml at build time.
# pyproject.toml is the single source of truth (see core/utils/version.py).
# The .dockerignore excludes any stale host src/mcp/VERSION, so this RUN
# is the only producer of /app/VERSION inside the image.
COPY pyproject.toml /tmp/pyproject.toml
RUN python -c "import tomllib,sys; v=tomllib.load(open('/tmp/pyproject.toml','rb'))['project']['version']; sys.stdout.write(v)" > /app/VERSION \
    && chown cerid:cerid /app/VERSION \
    && rm /tmp/pyproject.toml

USER cerid
EXPOSE 8888
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8888", \
     "--timeout-keep-alive", "30", \
     "--timeout-graceful-shutdown", "30"]
