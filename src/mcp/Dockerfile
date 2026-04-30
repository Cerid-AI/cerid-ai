# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Stage 1: Builder — install Python deps with build tools
FROM python:3.11.14-slim AS builder

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends gcc build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools

COPY requirements.lock .
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
FROM python:3.11.14-slim

# Disable chromadb's client-side posthog telemetry. Chromadb reads these at
# module import time, before any Python code can override via Settings(...).
# Aligns with cerid's self-hosted privacy-first default.
ENV ANONYMIZED_TELEMETRY=false \
    CHROMA_ANONYMIZED_TELEMETRY=false

# Pro plugins requiring tesseract-ocr and ffmpeg should install them in a
# plugin-specific layer or via the sidecar.  Core image stays minimal.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 cerid

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
# Copy preloaded models if they exist (no-op if CERID_PRELOAD_MODELS=false)
RUN mkdir -p /home/cerid/.cache/huggingface
COPY --from=models /root/.cache/huggingface/ /home/cerid/.cache/huggingface/
RUN chown -R cerid:cerid /home/cerid/.cache

WORKDIR /app
COPY --chown=cerid:cerid . .

ARG CERID_VERSION=""
RUN if [ -n "$CERID_VERSION" ]; then echo "$CERID_VERSION" > VERSION; fi

USER cerid
EXPOSE 8888
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8888", \
     "--timeout-keep-alive", "30", \
     "--timeout-graceful-shutdown", "30"]
