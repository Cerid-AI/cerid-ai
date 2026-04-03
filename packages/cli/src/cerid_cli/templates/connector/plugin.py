"""Cerid AI connector plugin: {name}"""
from __future__ import annotations

from plugins.base import ConnectorPlugin
from utils.data_sources.base import DataSource, DataSourceResult


class CustomDataSource(DataSource):
    name = "{name}"
    description = "Custom data source for {name}"
    enabled = True
    requires_api_key = False
    api_key_env_var = ""

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        # Implement your data source query logic here
        return []


class Plugin(ConnectorPlugin):
    name = "{name}"
    version = "0.1.0"
    description = "A custom connector plugin"

    def get_data_source(self):
        return CustomDataSource()


def register():
    return Plugin()
