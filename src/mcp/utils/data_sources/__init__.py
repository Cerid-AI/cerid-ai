# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""External data source framework -- pluggable APIs for knowledge enrichment.

Provides preloaded sources (Wikipedia, Wolfram Alpha, exchange rates) and a
registry for user-configurable custom REST API endpoints.

Dependencies: httpx (async HTTP), config/settings.py
Error types: none (source failures are silent -- never blocks retrieval)
"""

from .base import DataSource, DataSourceRegistry, DataSourceResult, registry
from .finance import ExchangeRatesSource
from .wikipedia import WikipediaSource
from .wolfram import WolframAlphaSource

# Auto-register preloaded sources
registry.register(WikipediaSource())
registry.register(WolframAlphaSource())
registry.register(ExchangeRatesSource())

__all__ = ["DataSource", "DataSourceResult", "DataSourceRegistry", "registry"]
