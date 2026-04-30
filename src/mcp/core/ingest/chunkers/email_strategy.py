# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Email chunker strategies (Workstream E Phase 2b.4).

* ``EmailHeader`` — single chunk; structured headers already
  rendered as the element's text. Metadata propagated unchanged so
  retrieval can filter by from/to/subject/thread_id.
* ``EmailBody`` — single chunk per email. Long bodies (rare for
  reply-stripped text) split via the legacy token chunker with the
  thread_id breadcrumb prepended on each piece so all of an email's
  body chunks remain joinable by ``thread_id``.
* ``EmailThreadEdge`` — emits a zero-text chunk that's effectively
  metadata-only. Phase 4's graph commit reads these to write
  ``REPLIES_TO`` Neo4j edges. The chunk-write path tolerates empty
  text (BM25 indexer skips it; ChromaDB stores the metadata).

The thread breadcrumb format ('Thread <thread_id>\\n\\n<body>') is
deliberately minimal — emails rarely benefit from a richer prefix
since the Subject is already in the EmailHeader chunk.
"""
from __future__ import annotations

from typing import Any

import config
from core.ingest.parsers import ParsedElement


def _thread_breadcrumb(metadata: dict[str, Any]) -> str:
    thread_id = metadata.get("thread_id") or ""
    return f"Thread {thread_id}" if thread_id else ""


def email_header_strategy(element: ParsedElement) -> list[dict[str, Any]]:
    """Pass-through: headers already rendered as searchable text."""
    return [
        {
            "text": element["text"],
            "metadata": {
                "element_type": "EmailHeader",
                **element.get("metadata", {}),
            },
        },
    ]


def email_body_strategy(element: ParsedElement) -> list[dict[str, Any]]:
    """Single chunk per body; split with breadcrumb replay if oversized."""
    body = element["text"]
    metadata = element.get("metadata", {})
    breadcrumb = _thread_breadcrumb(metadata)

    from utils.chunker import chunk_text, count_tokens

    max_tokens = getattr(config, "PARENT_CHUNK_TOKENS", 512)

    if not body:
        return []

    combined = f"{breadcrumb}\n\n{body}" if breadcrumb else body

    if count_tokens(combined) <= max_tokens:
        return [
            {
                "text": combined,
                "metadata": {
                    "element_type": "EmailBody",
                    **metadata,
                },
            },
        ]

    pieces = chunk_text(body, max_tokens=max_tokens)
    return [
        {
            "text": f"{breadcrumb}\n\n{piece}" if breadcrumb else piece,
            "metadata": {
                "element_type": "EmailBody",
                "body_chunk_idx": idx,
                **metadata,
            },
        }
        for idx, piece in enumerate(pieces)
    ]


def email_thread_edge_strategy(element: ParsedElement) -> list[dict[str, Any]]:
    """Emit a metadata-only chunk for Phase 4 graph-edge creation.

    The text is intentionally empty (no embedded representation
    needed); the BM25 indexer skips empty text and the chunk's
    metadata gets persisted so a future graph wiring can transform
    it into a Neo4j ``REPLIES_TO`` edge.
    """
    metadata = element.get("metadata", {})
    return [
        {
            "text": "",
            "metadata": {
                "element_type": "EmailThreadEdge",
                **metadata,
            },
        },
    ]


def register_default_strategies() -> None:
    """Register Phase 2b.4 strategies on the chunker registry."""
    from core.ingest.chunkers import register

    register("EmailHeader", email_header_strategy)
    register("EmailBody", email_body_strategy)
    register("EmailThreadEdge", email_thread_edge_strategy)
