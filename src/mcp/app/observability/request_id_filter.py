# src/mcp/app/observability/request_id_filter.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Logging filter that attaches the active request-id to every LogRecord.

Reads the request-id from ``core.utils.tracing.request_id_var`` (already
set by RequestIDMiddleware). Keeps the contextvar single-sourced — this
filter is read-only.

Use with a format string like::

    "%(asctime)s - %(name)s - %(request_id)s - %(levelname)s - %(message)s"

Emits ``"-"`` as the request-id outside a request lifecycle (startup,
shutdown, scheduler jobs, background tasks that never received an HTTP
request) so the format placeholder always resolves.
"""
from __future__ import annotations

import logging

from core.utils.tracing import get_request_id


class RequestIdFilter(logging.Filter):
    """Inject the active contextvar request-id into each LogRecord.

    Pairs with a format string referencing ``%(request_id)s``. Safe to
    install on every handler — the filter is idempotent and cheap.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # get_request_id() returns "" when unset; normalise to "-" so the
        # formatter never renders an awkward blank.
        record.request_id = get_request_id() or "-"
        return True
