# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""RQ task definitions (Workstream E Phase 5a — sync-default scaffold).

Each task is a sync function (RQ's contract) that wraps the existing
async ingestion service. Ingest is naturally async — we bridge by
running the coroutine on a fresh per-task event loop, so the worker
process (which itself is sync) doesn't pollute thread/loop state.

Idempotency contract: every task body uses ``chunk_id =
sha256(doc_hash:idx:chunk_hash)[:32]`` semantics inherited from
``services.ingestion.ingest_file`` (the canonical chunk-id formula
shipped in Phase 0). Re-enqueueing the same file is therefore safe;
the second run produces zero new chunks.

Logged stage: ``stage="ingest_worker"`` per the CLAUDE.md observability
contract — joins the same per-step trace timeline that interactive
ingest emits.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import structlog

logger = structlog.get_logger("ai-companion.queue.tasks")


def _run_async(coro: Any) -> Any:
    """Run a coroutine on a fresh event loop and return its result.

    Same pattern as :func:`core.utils.contextual._run_coro_isolated` —
    the loop is built and set inside a worker thread so the calling
    thread's event-loop state is untouched. Calling
    ``asyncio.set_event_loop`` on the main thread and then closing the
    loop pollutes every subsequent test in the same process; rq runs
    each task in its own subprocess in production, but the unit tests
    invoke the task body inline and would corrupt the pytest-asyncio
    loop without the thread isolation.
    """
    def _runner() -> Any:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_runner).result()


def ingest_file_task(
    *,
    file_path: str,
    domain: str = "",
    sub_category: str = "",
    tags: str = "",
    categorize_mode: str = "",
    client_source: str = "",
) -> dict[str, Any]:
    """Worker entrypoint that wraps :func:`services.ingestion.ingest_file`.

    Returns the same result dict the synchronous router path returns
    (``{"status": "...", "artifact_id": "...", "chunks": N, ...}``)
    so the queue path stays observationally identical.
    """
    # Lazy import — keeps the worker process boot light when the
    # ingestion service hasn't imported yet (cheap on cold-start).
    from app.services.ingestion import ingest_file

    log = logger.bind(stage="ingest_worker", file_path=file_path, domain=domain)
    log.info("ingest_file_task_started")

    result = _run_async(
        ingest_file(
            file_path=file_path,
            domain=domain,
            sub_category=sub_category,
            tags=tags,
            categorize_mode=categorize_mode,
            client_source=client_source,
        ),
    )

    log.info(
        "ingest_file_task_completed",
        status=result.get("status"),
        chunks=result.get("chunks"),
        artifact_id=result.get("artifact_id"),
    )
    return result


def ingest_content_task(
    *,
    content: str,
    domain: str = "general",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Worker entrypoint for raw-text ingestion via the queue."""
    from app.services.ingestion import ingest_content

    log = logger.bind(
        stage="ingest_worker",
        domain=domain,
        content_len=len(content) if content else 0,
    )
    log.info("ingest_content_task_started")

    # ingest_content is sync; no asyncio bridge needed.
    result = ingest_content(content, domain, metadata)

    log.info(
        "ingest_content_task_completed",
        status=result.get("status"),
        artifact_id=result.get("artifact_id"),
    )
    return result
