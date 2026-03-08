# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

FROM python:3.11.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

RUN useradd -m -u 1000 cerid && chown -R cerid:cerid /app
USER cerid

COPY --chown=cerid:cerid . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]
