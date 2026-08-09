"""Public import path for the query response envelope.

The implementation lives in ``core.models.query_envelope`` so the core
orchestrator can emit a degraded-path envelope without violating the
``core must not import app`` import-linter contract. This module is the
stable location for app-layer consumers (routers, tests, GUI contracts).
"""
from __future__ import annotations

from core.models.query_envelope import QueryEnvelope, SourceItem, SourceStatus

__all__ = ["QueryEnvelope", "SourceItem", "SourceStatus"]
