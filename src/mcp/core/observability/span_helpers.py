# src/mcp/core/observability/span_helpers.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Thin typed wrappers around sentry_sdk child-span creation for non-HTTP hot paths.

Using a helper rather than bare start_child calls:
  1. Keeps op / name / tag conventions consistent across the codebase
  2. No-ops cleanly when Sentry tracing is disabled (zero overhead)
  3. Provides a single breadcrumb helper with a stable category
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import sentry_sdk


@contextlib.contextmanager
def span(op: str, name: str, **tags: Any) -> Iterator["sentry_sdk.tracing.Span | None"]:
    """Start a Sentry child span; no-op when tracing is disabled.

    ``op`` follows the Sentry taxonomy ('db', 'http', 'retrieval.chroma',
    'retrieval.rerank', 'retrieval.nli', 'retrieval.assembly',
    'graph.expand', etc). Keep short; Sentry aggregates by (op, name).
    """
    current = sentry_sdk.get_current_span()
    if current is None or not hasattr(current, "start_child"):
        yield None
        return
    with current.start_child(op=op, description=name) as child:
        for key, value in tags.items():
            child.set_tag(key, value)
        yield child


def breadcrumb(message: str, category: str = "cerid", data: dict[str, Any] | None = None) -> None:
    """Cheap timeline marker for Sentry. No-op when Sentry is not initialised."""
    sentry_sdk.add_breadcrumb(message=message, category=category, data=data or {})
