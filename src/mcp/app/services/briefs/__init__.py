# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Brief generation service package.

Public surface
--------------
``BriefService``  — generates, stores, and retrieves daily / weekly briefs.
``BriefRecord``   — Pydantic model for a generated brief.
"""
from __future__ import annotations

from app.services.briefs.service import BriefRecord, BriefService

__all__ = ["BriefRecord", "BriefService"]
