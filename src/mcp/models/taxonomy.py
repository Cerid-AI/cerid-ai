# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed response models for the ``/taxonomy`` and ``/tags`` endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "TaxonomyResponse",
    "CreateDomainResponse",
    "CreateSubCategoryResponse",
    "TagItem",
    "TagSuggestionItem",
]


class _TaxonomyBase(BaseModel):
    """Base for all taxonomy response models — allows extra fields for forward compat."""

    model_config = ConfigDict(extra="allow")


class TaxonomyResponse(_TaxonomyBase):
    """Response from ``GET /taxonomy`` — full taxonomy tree."""

    domains: dict[str, Any] = Field(default_factory=dict, description="Domain tree with sub-categories and artifact counts")
    tags: list[dict[str, Any]] = Field(default_factory=list, description="Top tags with usage counts")


class CreateDomainResponse(_TaxonomyBase):
    """Response from ``POST /taxonomy/domain``."""

    name: str = Field(description="Domain name (lowercase)")
    description: str = Field(default="", description="Domain description")
    icon: str = Field(default="file", description="Icon identifier")
    sub_categories: list[str] = Field(default_factory=list, description="Created sub-category labels")


class CreateSubCategoryResponse(_TaxonomyBase):
    """Response from ``POST /taxonomy/subcategory``."""

    domain: str = Field(description="Parent domain name")
    sub_category: str = Field(description="Created sub-category label")


class TagItem(_TaxonomyBase):
    """A single tag with usage count."""

    name: str = Field(description="Tag name")
    usage_count: int = Field(default=0, ge=0, description="Number of artifacts using this tag")


class TagSuggestionItem(_TaxonomyBase):
    """A tag suggestion for typeahead."""

    name: str = Field(description="Tag name")
    source: str = Field(description="Origin: 'vocabulary' or 'existing'")
    usage_count: int = Field(default=0, ge=0, description="Current usage count")
