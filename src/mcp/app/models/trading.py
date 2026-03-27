"""Pydantic request models for trading SDK endpoints."""
from __future__ import annotations

from pydantic import BaseModel, Field


class TradingSignalRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    signal_data: dict = Field(default_factory=dict)
    domains: list[str] = Field(default_factory=lambda: ["finance", "trading"])
    top_k: int = Field(default=5, ge=1, le=50)


class HerdDetectRequest(BaseModel):
    asset: str = Field(..., min_length=1, max_length=10)
    sentiment_data: dict = Field(default_factory=dict)


class KellySizeRequest(BaseModel):
    strategy: str = Field(..., min_length=1, max_length=50)
    confidence: float = Field(..., ge=0.0, le=1.0)
    win_loss_ratio: float = Field(..., ge=0.0, le=100.0)


class CascadeConfirmRequest(BaseModel):
    asset: str = Field(..., min_length=1, max_length=10)
    liquidation_events: list[dict] = Field(default_factory=list)


class LongshotSurfaceRequest(BaseModel):
    asset: str = Field(..., min_length=1, max_length=10)
    date_range: str = Field(default="30d", pattern=r"^\d+d$")
