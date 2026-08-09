# Plugin Development Deep Dive

Internals of the Cerid AI plugin system for developers building production
plugins. For a quickstart, see [plugins/README.md](../plugins/README.md).

## Plugin Lifecycle

`register_all_plugins()` in `src/mcp/plugins/__init__.py` runs at startup:

1. **Discovery** -- Scans `src/mcp/plugins/` then `plugins/` at repo root.
   Directories starting with `_` or `.` are skipped.
2. **Manifest validation** -- `manifest.json` must contain `name`, `version`,
   `type`. Invalid manifests raise `PluginLoadError`.
3. **Enable check** -- If `ENABLED_PLUGINS` is set, only listed plugins load.
4. **Tier check** -- Manifest `tier` (default: `community`) is checked against
   `CERID_TIER` via `is_tier_met()`. Unmet tiers are skipped.
5. **Dependency check** -- Each `requires` entry is imported. Missing packages
   skip the plugin with an install command in the warning.
6. **Module load** -- `plugin.py` loaded via `importlib.util`, registered in
   `sys.modules` as `cerid_plugin_{name}`.
7. **Registration** -- Module-level `register()` function is called.
8. **Post-load** -- Plugin info stored in `_loaded_plugins`. After all plugins
   load, `on_startup()` is called on each.

## Base Classes

All in `src/mcp/plugins/base.py`.

### CeridPlugin (abstract root)

| Member | Required | Description |
|--------|----------|-------------|
| `name` (property) | Yes | Unique string identifier |
| `version` (property) | Yes | Semantic version string |
| `description` (property) | No | Default: `""` |
| `register()` | Yes | Hook into the system during loading |
| `on_startup()` | No | Called after all plugins loaded |
| `on_shutdown()` | No | Called during app shutdown |

### ParserPlugin

Implement `get_parsers()` returning `dict[str, Callable[[str], dict]]`. Each
parser receives a file path, returns `{"text": str, "file_type": str,
"page_count": int | None}`. The base `register()` adds entries to
`parsers.PARSER_REGISTRY`, overriding existing extensions with an info log.

### AgentPlugin

Implement `get_routes()` returning a list of `fastapi.APIRouter`. Routers are
collected and mounted on the app after all plugins load.

### SyncBackendPlugin

Implement `get_backend_class()` (returns `SyncBackend` subclass) and
`get_backend_name()` (returns identifier string like `"s3"`).

## Manifest Schema

```json
{
  "name": "string (required)",
  "version": "string (required)",
  "type": "parser | agent | sync | middleware (required)",
  "description": "string (optional)",
  "tier": "community | pro | enterprise (optional, default: community)",
  "requires": ["package>=version (optional)"]
}
```

## Registering FastAPI Routers

Use a prefix under `/plugins/` to avoid collisions with core routes:

```python
router = APIRouter(prefix="/plugins/my-agent", tags=["MyAgent"])
```

## Adding MCP Tools

Register tools in `register()` using the `@mcp_tool()` decorator:

```python
from tools import mcp_tool

def register():
    @mcp_tool(name="pkb_my_tool", description="Does something useful")
    async def my_tool(param: str) -> dict:
        return {"result": param.upper()}
```

Use the `pkb_` prefix convention for consistency with built-in tools.

## Feature Gating

Use the same patterns as core code:

```python
from config.features import require_feature, check_feature

@require_feature("my_plugin_feature")
async def gated_endpoint():
    ...

if check_feature("advanced_analysis"):
    run_advanced_path()
```

Add plugin-specific flags to `config/features.py` and document in
`docs/TIER_MATRIX.md`.

## Testing Patterns

### Parser plugin

```python
def test_parser_registers():
    with patch("parsers.PARSER_REGISTRY", {}) as registry:
        from my_plugin.plugin import register
        register()
    assert ".xyz" in registry
```

### Agent plugin

```python
from fastapi.testclient import TestClient
from fastapi import FastAPI

def test_agent_routes():
    from my_agent.plugin import _instance
    app = FastAPI()
    for r in _instance.get_routes():
        app.include_router(r)
    client = TestClient(app)
    assert client.post("/plugins/my-agent/analyze", json={}).status_code == 200
```

### Integration

Start the stack with your plugin in `plugins/` and verify the load log:
`Plugin loaded: my-plugin v1.0.0 (type: parser)`

## Error Handling

Plugin failures are caught and logged. A failing plugin does not prevent other
plugins or the application from starting. Use `CeridError` subclasses within
plugin code for consistency with core error handling.

## Publishing and Distribution

Community plugins are distributed as directories. To share a plugin:

1. Create a repo with `manifest.json` + `plugin.py` + supporting modules.
2. Document `requires` dependencies clearly.
3. Users clone or copy the directory into `plugins/`.

No centralized registry exists. Plugins load from the local filesystem only.
For commercial plugins, set `"tier": "pro"` or `"enterprise"` in the manifest.
