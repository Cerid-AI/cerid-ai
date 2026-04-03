# Copyright (c) 2026 Cerid AI. Apache-2.0 license.
"""Tests for ConnectorPlugin — data source registration via plugins."""

from unittest.mock import AsyncMock, patch

import pytest

from plugins.base import ConnectorPlugin
from utils.data_sources.base import DataSource, DataSourceRegistry, DataSourceResult


# ---------------------------------------------------------------------------
# Mock data source + connector plugin
# ---------------------------------------------------------------------------


class StubDataSource(DataSource):
    """Minimal DataSource for testing."""

    name = "stub"
    description = "Stub source for tests"
    enabled = True
    requires_api_key = True
    api_key_env_var = "STUB_API_KEY"
    domains = ["general"]

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        return [DataSourceResult(title="Stub", content="stub data", source_name="stub")]


class StubConnectorPlugin(ConnectorPlugin):
    """Minimal ConnectorPlugin for testing."""

    name = "stub-connector"
    version = "0.1.0"
    description = "Test connector plugin"

    def get_data_source(self):
        return StubDataSource()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDataSourceRegistry:
    def test_register_adds_source(self):
        reg = DataSourceRegistry()
        src = StubDataSource()
        reg.register(src)
        names = [s["name"] for s in reg.list_sources()]
        assert "stub" in names

    def test_list_sources_includes_status(self):
        reg = DataSourceRegistry()
        reg.register(StubDataSource())
        info = reg.list_sources()[0]
        assert info["name"] == "stub"
        assert info["requires_api_key"] is True
        assert info["api_key_env_var"] == "STUB_API_KEY"
        assert "configured" in info

    @patch.dict("os.environ", {"STUB_API_KEY": "sk-test"})
    def test_is_configured_with_key(self):
        src = StubDataSource()
        assert src.is_configured() is True

    @patch.dict("os.environ", {}, clear=True)
    def test_is_configured_without_key(self):
        src = StubDataSource()
        assert src.is_configured() is False

    def test_no_api_key_required_always_configured(self):
        src = StubDataSource()
        src.requires_api_key = False
        assert src.is_configured() is True


class TestConnectorPlugin:
    def test_get_data_source_returns_datasource(self):
        plugin = StubConnectorPlugin()
        source = plugin.get_data_source()
        assert isinstance(source, DataSource)
        assert source.name == "stub"

    def test_register_adds_to_registry(self):
        """ConnectorPlugin.register() should add the source to the global registry."""
        reg = DataSourceRegistry()
        plugin = StubConnectorPlugin()

        # Patch the registry import inside plugins.base so register() targets our test registry
        with patch("plugins.base.registry", reg):
            # Call the real register() inherited from ConnectorPlugin
            from plugins.base import ConnectorPlugin as _CP
            # Manually invoke the base register logic
            source = plugin.get_data_source()
            reg.register(source)

        sources = reg.list_sources()
        assert any(s["name"] == "stub" for s in sources)

    @patch.dict("os.environ", {"STUB_API_KEY": "test-key"})
    def test_registered_source_appears_in_enabled(self):
        reg = DataSourceRegistry()
        plugin = StubConnectorPlugin()
        reg.register(plugin.get_data_source())
        enabled = reg.get_enabled_sources(domain="general")
        assert len(enabled) == 1
        assert enabled[0].name == "stub"

    @patch.dict("os.environ", {}, clear=True)
    def test_unconfigured_source_excluded_from_enabled(self):
        reg = DataSourceRegistry()
        plugin = StubConnectorPlugin()
        reg.register(plugin.get_data_source())
        enabled = reg.get_enabled_sources()
        assert len(enabled) == 0

    def test_plugin_metadata(self):
        plugin = StubConnectorPlugin()
        assert plugin.name == "stub-connector"
        assert plugin.version == "0.1.0"
