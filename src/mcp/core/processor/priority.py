# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Job priority levels and dequeue ordering.

Three levels keeps the scheduling model simple and auditable. The
``priority_order()`` helper is the single source of truth for dequeue
iteration so callers never hard-code the order.
"""
from __future__ import annotations

from enum import Enum


class Priority(str, Enum):
    """Three-tier job priority.

    Inheriting ``str`` lets instances be JSON-serialised without a
    custom encoder and compared to string literals in dict payloads.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def priority_order() -> list[Priority]:
    """Return priorities in dequeue-first order (highest urgency first)."""
    return [Priority.HIGH, Priority.MEDIUM, Priority.LOW]


# Relative weighting used for tie-breaking within the same priority
# bucket (e.g. when multiple jobs share a priority and FIFO is
# insufficient — cost-aware schedulers can consult these).
PRIORITY_WEIGHT: dict[Priority, int] = {
    Priority.HIGH: 100,
    Priority.MEDIUM: 50,
    Priority.LOW: 10,
}
