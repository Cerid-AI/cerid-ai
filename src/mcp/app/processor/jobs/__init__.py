# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Concrete BaseJob subclasses for the Cerid background processor."""
from __future__ import annotations

from app.processor.jobs.brief_generation import BriefGenerationJob
from app.processor.jobs.compute_entity_embeddings import ComputeEntityEmbeddingsJob
from app.processor.jobs.compute_trust_state import ComputeTrustStateJob
from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob
from app.processor.jobs.config_recommender import ConfigRecommenderJob
from app.processor.jobs.entity_extraction import EntityExtractionJob
from app.processor.jobs.hype_indexing import HyPEIndexingJob
from app.processor.jobs.ingest_recovery import IngestRecoveryJob
from app.processor.jobs.memory_entity_extraction import MemoryEntityExtractionJob
from app.processor.jobs.memory_extract import MemoryExtractJob
from app.processor.jobs.weekly_synthesis import WeeklySynthesisJob
from app.processor.jobs.wiki_refresh import WikiRefreshJob

__all__ = [
    "BriefGenerationJob",
    "ComputeEntityEmbeddingsJob",
    "ComputeTrustStateJob",
    "ComputeUmap3DJob",
    "ConfigRecommenderJob",
    "EntityExtractionJob",
    "HyPEIndexingJob",
    "IngestRecoveryJob",
    "MemoryEntityExtractionJob",
    "MemoryExtractJob",
    "WeeklySynthesisJob",
    "WikiRefreshJob",
]
