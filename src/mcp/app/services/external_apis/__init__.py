# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Public-API adapters — curated set of eight keyless external APIs.

All adapters extend :class:`app.services.external_apis.base.ExternalAPIAdapter`.
Import adapters by their concrete class or via the registry:

    from app.services.external_apis import WikipediaAdapter
    from app.services.external_apis.registry import list_adapters
"""
from __future__ import annotations

from app.services.external_apis.arxiv import ArxivAdapter
from app.services.external_apis.base import ExternalAPIAdapter, ExternalAPIError
from app.services.external_apis.github import GitHubAdapter
from app.services.external_apis.openlibrary import OpenLibraryAdapter
from app.services.external_apis.osm import OSMAdapter
from app.services.external_apis.packages import PackagesAdapter
from app.services.external_apis.stackexchange import StackExchangeAdapter
from app.services.external_apis.wikidata import WikidataAdapter
from app.services.external_apis.wikipedia import WikipediaAdapter

__all__ = [
    "ExternalAPIAdapter",
    "ExternalAPIError",
    "WikipediaAdapter",
    "WikidataAdapter",
    "OpenLibraryAdapter",
    "StackExchangeAdapter",
    "ArxivAdapter",
    "GitHubAdapter",
    "PackagesAdapter",
    "OSMAdapter",
]
