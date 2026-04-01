# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""External data source framework -- pluggable APIs for knowledge enrichment.

Provides preloaded sources (Wikipedia, Wolfram Alpha, exchange rates) and a
registry for user-configurable custom REST API endpoints.

Dependencies: httpx (async HTTP), config/settings.py
Error types: none (source failures are silent -- never blocks retrieval)
"""

from .base import DataSource, DataSourceRegistry, DataSourceResult, registry
from .bookmarks import BookmarksSource
from .duckduckgo import DuckDuckGoSource
from .email_imap import EmailImapSource
from .finance import ExchangeRatesSource
from .openlibrary import OpenLibrarySource
from .pubchem import PubChemSource
from .rss_feed import RSSFeedSource
from .wikipedia import WikipediaSource
from .wolfram import WolframAlphaSource

# Auto-register preloaded sources
registry.register(WikipediaSource())
registry.register(WolframAlphaSource())
registry.register(ExchangeRatesSource())
registry.register(DuckDuckGoSource())
registry.register(OpenLibrarySource())
registry.register(PubChemSource())
registry.register(BookmarksSource())
registry.register(EmailImapSource())
registry.register(RSSFeedSource())

__all__ = ["DataSource", "DataSourceResult", "DataSourceRegistry", "registry"]
