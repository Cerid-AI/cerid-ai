# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Abstract base classes for Cerid AI plugins.

Plugin developers should subclass these to implement custom functionality.
Each plugin type has a specific contract for what register() should do.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any


class CeridPlugin(ABC):
    """Base class for all Cerid AI plugins."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin identifier."""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic version string."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description."""
        return ""

    @abstractmethod
    def register(self) -> None:
        """
        Called during plugin loading to register capabilities.

        Implementations should call the appropriate registry functions
        to hook into the system (e.g., register_parser, add routes).
        """
        ...

    def on_startup(self) -> None:
        """Optional hook called after all plugins are loaded."""
        pass

    def on_shutdown(self) -> None:
        """Optional hook called during app shutdown."""
        pass


class ParserPlugin(CeridPlugin):
    """
    Plugin that registers file parsers.

    Subclass and implement get_parsers() to return a mapping of
    file extensions to parser functions. The register() method
    will automatically add them to the parser registry.

    Example:
        class OCRPlugin(ParserPlugin):
            name = "ocr"
            version = "1.0.0"

            def get_parsers(self):
                return {
                    ".pdf": self.parse_pdf_with_ocr,
                    ".tiff": self.parse_image_with_ocr,
                }

            def parse_pdf_with_ocr(self, file_path: str) -> dict:
                ...
    """

    @abstractmethod
    def get_parsers(self) -> dict[str, Callable[[str], dict[str, Any]]]:
        """
        Return mapping of file extensions to parser functions.

        Each parser function should accept a file path and return:
            {"text": str, "file_type": str, "page_count": int | None}
        """
        ...

    def register(self) -> None:
        from parsers import PARSER_REGISTRY

        for ext, parser_fn in self.get_parsers().items():
            ext_lower = ext.lower()
            if ext_lower in PARSER_REGISTRY:
                from logging import getLogger
                getLogger("ai-companion.plugins").info(
                    f"Plugin '{self.name}' overriding parser for {ext_lower}"
                )
            PARSER_REGISTRY[ext_lower] = parser_fn


class AgentPlugin(CeridPlugin):
    """
    Plugin that registers agent workflows.

    Subclass and implement get_routes() to return FastAPI router(s)
    that will be mounted on the app.
    """

    @abstractmethod
    def get_routes(self) -> list:
        """Return list of FastAPI APIRouter instances to mount."""
        ...

    def register(self) -> None:
        # Routes are collected and mounted by the plugin loader
        # after all plugins are loaded
        pass


class SyncBackendPlugin(CeridPlugin):
    """
    Plugin that provides a sync backend implementation.

    Subclass and implement the sync backend interface methods.
    See utils/sync_backend.py for the SyncBackend ABC.
    """

    @abstractmethod
    def get_backend_class(self) -> type:
        """Return the SyncBackend subclass provided by this plugin."""
        ...

    @abstractmethod
    def get_backend_name(self) -> str:
        """Return the name used to select this backend (e.g., 's3', 'webdav')."""
        ...

    def register(self) -> None:
        # Sync backends are collected and made available via config
        pass
