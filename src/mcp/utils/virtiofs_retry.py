# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retry decorator for macOS Docker virtiofs Errno 35 (EDEADLK).

The virtiofs filesystem used by Docker Desktop for Mac has known issues
with concurrent file access across the host/container boundary.  This
decorator retries file operations that hit the deadlock error.
"""
from __future__ import annotations

import errno
import functools
import logging
import time

logger = logging.getLogger("ai-companion.virtiofs")


def virtiofs_retry(attempts: int = 3, base_delay: float = 0.5):
    """Retry file operations that may hit virtiofs EDEADLK.

    Backs off exponentially: 0.5s → 1s → 2s by default.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except OSError as exc:
                    if exc.errno not in (errno.EDEADLK, 35) or attempt == attempts - 1:
                        raise
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "virtiofs Errno 35 in %s, retry %d/%d in %.1fs",
                        fn.__name__, attempt + 1, attempts, delay,
                    )
                    time.sleep(delay)
        return wrapper
    return decorator
