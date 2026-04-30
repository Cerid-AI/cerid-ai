# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ingest queue worker entrypoint (Workstream E Phase 5a).

Run inside the existing MCP container so paths and credentials match
the synchronous path:

    docker compose exec ai-companion-mcp \\
        python -m app.queue.worker

Or as a separate compose service (cleanest production shape):

    services:
      cerid-ingest-worker:
        image: cerid-ai-mcp:latest
        command: ["python", "-m", "app.queue.worker"]
        depends_on: [redis, neo4j, chroma]
        environment:
          - INGEST_QUEUE_MODE=async
        volumes:
          - ./data:/app/data         # share BM25 corpus + chunk hashes
          - ./archive:/archive

The worker only fires when ``INGEST_QUEUE_MODE=async``; it logs and
exits cleanly when the env says ``sync`` so a misconfigured deploy
doesn't silently dual-process.
"""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger("ai-companion.queue.worker")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from app.queue import _rq_available, get_ingest_queue, is_async_mode

    if not _rq_available:
        logger.error(
            "rq is not installed — install rq>=2.0 in this image to run a worker.",
        )
        return 2

    if not is_async_mode():
        logger.error(
            "INGEST_QUEUE_MODE is not 'async' — refusing to start the worker. "
            "Set INGEST_QUEUE_MODE=async in the worker container env.",
        )
        return 3

    # Lazy import so missing rq doesn't crash the module load above.
    from rq import Worker  # type: ignore[import-not-found]

    queue = get_ingest_queue()
    worker = Worker([queue], connection=queue.connection)
    logger.info("Starting ingest worker on queue=%s", queue.name)
    worker.work(with_scheduler=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
