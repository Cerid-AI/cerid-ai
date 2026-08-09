"""Cerid AI tool plugin: {name}"""
from __future__ import annotations

from plugins.base import ToolPlugin


class Plugin(ToolPlugin):
    name = "{name}"
    version = "0.1.0"
    description = "A custom tool plugin"

    def get_tools(self):
        return [
            {
                "name": "plg_{name}_search",
                "description": "Search via {name}",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                    },
                    "required": ["query"],
                },
                "handler": self.handle_search,
            },
        ]

    async def handle_search(self, arguments: dict):
        query = arguments.get("query", "")
        return {"results": [], "query": query}


def register():
    return Plugin()
