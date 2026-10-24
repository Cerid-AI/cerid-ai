# Cerid AI Plugin System

Plugins are self-contained extensions that add capabilities to Cerid AI without
modifying core code. They are discovered at startup, validated against a manifest,
and loaded into the running application.

## Plugin Types

Four plugin types are supported, defined in `src/mcp/plugins/base.py`:

| Type | Base Class | Purpose |
|------|-----------|---------|
| `parser` | `ParserPlugin` | Register file parsers for new formats |
| `agent` | `AgentPlugin` | Add agent workflows with FastAPI routes |
| `sync` | `SyncBackendPlugin` | Provide sync backend implementations (S3, WebDAV) |
| `middleware` | `CeridPlugin` | Add request/response middleware |

## Directory Structure

```
my-plugin/
  manifest.json     # Plugin metadata (required)
  plugin.py         # Entry point with register() function (required)
```

## Manifest Format

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "type": "parser",
  "description": "Adds support for XYZ file format",
  "tier": "community",
  "requires": ["pillow>=10.0"]
}
```

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | Yes | -- | Unique plugin identifier |
| `version` | Yes | -- | Semantic version string |
| `type` | Yes | -- | One of: `parser`, `agent`, `sync`, `middleware` |
| `description` | No | `""` | Human-readable summary |
| `tier` | No | `"community"` | Minimum tier: `community`, `pro`, or `enterprise` |
| `requires` | No | `[]` | Python package dependencies checked before loading |

## Creating a Plugin

### Parser Example

```python
# my-ocr-plugin/plugin.py
from plugins.base import ParserPlugin

class OCRPlugin(ParserPlugin):
    name = "my-ocr-plugin"
    version = "1.0.0"

    def get_parsers(self):
        return {".tiff": self._parse_image}

    def _parse_image(self, file_path: str) -> dict:
        import pytesseract
        from PIL import Image
        text = pytesseract.image_to_string(Image.open(file_path))
        return {"text": text, "file_type": "image/tiff", "page_count": 1}

_instance = OCRPlugin()

def register():
    _instance.register()
```

The module-level `register()` function is the entry point the loader calls.

### Agent Example

```python
# my-agent/plugin.py
from fastapi import APIRouter
from plugins.base import AgentPlugin

router = APIRouter(prefix="/plugins/my-agent", tags=["MyAgent"])

@router.post("/analyze")
async def analyze(data: dict):
    return {"result": "analysis complete"}

class MyAgentPlugin(AgentPlugin):
    name = "my-agent"
    version = "1.0.0"
    def get_routes(self):
        return [router]

_instance = MyAgentPlugin()

def register():
    _instance.register()
```

## Loading Plugins

Plugins are discovered from two locations at startup:

1. `src/mcp/plugins/` -- in-tree plugins (scanned first)
2. `plugins/` at the repository root -- external/commercial plugins

Set `CERID_PLUGIN_DIR` to load from a custom directory. Set `ENABLED_PLUGINS`
to a comma-separated list of plugin names to restrict which plugins load.

## Tier System

- `"community"` (default) -- loads on any tier, including the free core.
- `"pro"` -- requires `CERID_TIER=pro` or `CERID_TIER=enterprise`.
- `"enterprise"` -- requires `CERID_TIER=enterprise`.

Plugins that do not meet the tier requirement are skipped with an info log.

## Lifecycle Hooks

Optional methods from `CeridPlugin`:

- `on_startup()` -- called after all plugins are loaded.
- `on_shutdown()` -- called during application shutdown.

## Testing Your Plugin

1. Place your plugin directory in `plugins/`.
2. Start the stack: `./scripts/start-cerid.sh --build`
3. Check logs: `Plugin loaded: my-ocr-plugin v1.0.0 (type: parser)`

For unit tests, mock the registry:

```python
from unittest.mock import patch

def test_parser_registers():
    with patch("parsers.PARSER_REGISTRY", {}) as registry:
        from my_ocr_plugin.plugin import register
        register()
        assert ".tiff" in registry
```

## Dependency Handling

Missing `requires` packages cause the plugin to be skipped with a warning:

```
Plugin 'my-ocr-plugin' missing dependencies: ['pytesseract>=0.3'].
Install with: pip install pytesseract>=0.3
```

Install dependencies before starting the stack, or add them to your Docker image.

See [docs/PLUGIN_DEVELOPMENT.md](../docs/PLUGIN_DEVELOPMENT.md) for the full
lifecycle, manifest schema reference, and advanced patterns.
