# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cerid background processor — app-layer orchestration package.

Public API
----------
ProcessorWorker      — async worker that drains the job queue
build_default_registry — build the job_type → class mapping
router               — FastAPI router (prefix: /processor)
"""
from __future__ import annotations

from app.processor.router import router
from app.processor.worker import ProcessorWorker, build_default_registry

__all__ = [
    "ProcessorWorker",
    "build_default_registry",
    "router",
]
