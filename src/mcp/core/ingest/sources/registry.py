# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Module-level registry of SourceConnector instances, keyed by kind.

Connector modules call :func:`register_connector` at import time
(in the package's ``__init__`` or via explicit module-load). The
FE-facing ``GET /sources/kinds`` endpoint enumerates this registry.

Registration is idempotent — re-registering the same kind logs a
debug message rather than raising, so test fixtures can install
mock connectors without disturbing prod state.
"""
from __future__ import annotations

import logging
from typing import Iterator

from core.ingest.sources.base import SourceConnector
from core.ingest.sources.kinds import SourceKind

logger = logging.getLogger("ai-companion.ingest.sources.registry")

# Module-level mutable. Reset between tests via the
# ``reset_registry_for_tests`` helper below.
_REGISTRY: dict[SourceKind, SourceConnector] = {}


def register_connector(connector: SourceConnector) -> None:
    """Register a connector instance under its declared kind. Idempotent."""
    if connector.kind in _REGISTRY:
        if _REGISTRY[connector.kind] is connector:
            return  # already registered same instance — no-op
        logger.debug(
            "Re-registering connector kind=%s (replacing %r with %r)",
            connector.kind,
            _REGISTRY[connector.kind],
            connector,
        )
    _REGISTRY[connector.kind] = connector


def get_connector(kind: SourceKind) -> SourceConnector | None:
    """Return the registered connector for ``kind`` or None if not registered."""
    return _REGISTRY.get(kind)


def iter_connectors() -> Iterator[SourceConnector]:
    """Iterate all registered connectors in registration order."""
    return iter(_REGISTRY.values())


def reset_registry_for_tests() -> None:
    """Clear the registry. Tests only — do not call from app code."""
    _REGISTRY.clear()
