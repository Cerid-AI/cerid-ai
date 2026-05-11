# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Concrete BaseJob subclasses for the Cerid background processor."""
from __future__ import annotations

from app.processor.jobs.brief_generation import BriefGenerationJob
from app.processor.jobs.entity_extraction import EntityExtractionJob
from app.processor.jobs.ingest_recovery import IngestRecoveryJob
from app.processor.jobs.weekly_synthesis import WeeklySynthesisJob
from app.processor.jobs.wiki_refresh import WikiRefreshJob

__all__ = [
    "BriefGenerationJob",
    "EntityExtractionJob",
    "IngestRecoveryJob",
    "WeeklySynthesisJob",
    "WikiRefreshJob",
]
