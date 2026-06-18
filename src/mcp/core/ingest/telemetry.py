# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Connection-time telemetry for the Sources gamification surface.

Each source-add flow timestamps the user's first click in the wizard
and the connector's :meth:`connect` returning success. The delta is
the "you connected this in 8.2s" number surfaced in F3 (wizard),
F4 (source-detail header), and the F9 HUD aggregate.

Wall-clock by design — the metric is the user-perceived time, not
the network-cost time. If OAuth takes 12s because Google's consent
screen is slow, that's what the metric shows. The honesty is the
feature.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

logger = logging.getLogger("ai-companion.ingest.telemetry")


@dataclass
class ConnectTimer:
    """Single-connect timer. Used as a context manager:

    .. code-block:: python

        with ConnectTimer() as t:
            await connector.connect(config)
        elapsed_ms = t.elapsed_ms
    """

    started_ns: int = 0
    ended_ns: int = 0

    @property
    def elapsed_ms(self) -> int:
        """Milliseconds between start and end. Returns 0 if not yet ended."""
        if self.ended_ns == 0:
            return 0
        return (self.ended_ns - self.started_ns) // 1_000_000

    def __enter__(self) -> "ConnectTimer":
        self.started_ns = time.perf_counter_ns()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        self.ended_ns = time.perf_counter_ns()


@contextmanager
def time_connect() -> Iterator[ConnectTimer]:
    """Convenience wrapper. Yields a :class:`ConnectTimer` that auto-
    stops on context exit; the caller reads ``timer.elapsed_ms`` after."""
    timer = ConnectTimer()
    with timer:
        yield timer
