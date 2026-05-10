# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Background processor — pure-logic package.

Public surface for ``core/processor/``. Importing from this package is
preferred over importing individual submodules directly; it keeps the
import path stable if internal structure changes.
"""
from __future__ import annotations

from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobRecord, JobResult, JobState
from core.processor.priority import Priority
from core.processor.queue import JobQueueProtocol

__all__ = [
    "BaseJob",
    "CostEstimate",
    "JobQueueProtocol",
    "JobRecord",
    "JobResult",
    "JobState",
    "Priority",
]
