# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

FROM python:3.11.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

RUN apt-get purge -y --auto-remove gcc build-essential && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 cerid && chown -R cerid:cerid /app
USER cerid

# Pre-download cross-encoder reranker model (baked into image, ~91 MB)
RUN python -c "\
from huggingface_hub import hf_hub_download; \
hf_hub_download('cross-encoder/ms-marco-MiniLM-L-6-v2', 'onnx/model.onnx'); \
hf_hub_download('cross-encoder/ms-marco-MiniLM-L-6-v2', 'tokenizer.json')"

COPY --chown=cerid:cerid . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]
