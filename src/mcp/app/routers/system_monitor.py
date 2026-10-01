# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Storage metrics endpoint.

Endpoints:
  /system/storage         — Aggregated storage usage across all data stores

The per-store aggregation + usage-pct/threshold math lives in
``app/services/storage_metrics.py`` — shared with the ingest backpressure
check in ``app/services/ingestion.py`` (AF-042) so there is exactly one
computation of "how full is the corpus".
"""
from __future__ import annotations

from fastapi import APIRouter

from app.services.storage_metrics import get_storage_report

router = APIRouter()


@router.get("/system/storage")  # response-model-allowed: dynamic response (shape varies)
def get_storage_metrics():
    """Return storage usage across all data stores, cached for 60 seconds."""
    return get_storage_report()
