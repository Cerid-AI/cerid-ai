# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Plugin management CLI commands — create, test, list."""
from __future__ import annotations

import json
import os
from pathlib import Path

import click
import httpx

# Template directory lives next to this file
_TEMPLATE_DIR = Path(__file__).parent / "templates"

_VALID_TYPES = ("tool", "connector", "parser", "agent")


@click.group()
def plugin() -> None:
    """Plugin scaffolding, testing, and registry commands."""


@plugin.command("create")
@click.argument("name")
@click.option("--type", "plugin_type", type=click.Choice(_VALID_TYPES), default="tool", help="Plugin type to scaffold.")
def create(name: str, plugin_type: str) -> None:
    """Scaffold a new plugin directory with manifest.json and plugin.py."""
    target = Path.cwd() / name
    if target.exists():
        click.echo(f"Error: Directory '{name}' already exists.", err=True)
        raise SystemExit(1)

    # Load templates — fall back to tool templates if type-specific ones are missing
    template_dir = _TEMPLATE_DIR / plugin_type
    if not template_dir.exists():
        template_dir = _TEMPLATE_DIR / "tool"

    manifest_tpl = (template_dir / "manifest.json").read_text()
    plugin_tpl = (template_dir / "plugin.py").read_text()

    # Simple string replacement (no Jinja2 needed for {name} placeholders)
    manifest_text = manifest_tpl.replace("{name}", name).replace("{type}", plugin_type)
    plugin_text = plugin_tpl.replace("{name}", name).replace("{type}", plugin_type)

    target.mkdir(parents=True)
    (target / "manifest.json").write_text(manifest_text)
    (target / "plugin.py").write_text(plugin_text)
    click.echo(f"Created {plugin_type} plugin scaffold at ./{name}/")


@plugin.command("test")
@click.option("--dir", "plugin_dir", default=".", help="Plugin directory to validate.")
def test(plugin_dir: str) -> None:
    """Validate a plugin manifest and attempt to import plugin.py."""
    base = Path(plugin_dir).resolve()
    manifest_path = base / "manifest.json"
    plugin_path = base / "plugin.py"

    if not manifest_path.exists():
        click.echo(f"Error: No manifest.json found in {base}", err=True)
        raise SystemExit(1)

    # Validate manifest JSON
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except json.JSONDecodeError as exc:
        click.echo(f"Error: Invalid JSON in manifest.json: {exc}", err=True)
        raise SystemExit(1)

    required_keys = {"name", "version", "type"}
    missing = required_keys - set(manifest.keys())
    if missing:
        click.echo(f"Error: manifest.json missing required keys: {missing}", err=True)
        raise SystemExit(1)

    click.echo(f"  manifest.json: OK ({manifest['name']} v{manifest['version']}, type={manifest['type']})")

    # Try import
    if plugin_path.exists():
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location("plugin", str(plugin_path))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                click.echo("  plugin.py: OK (imported successfully)")
            else:
                click.echo("  plugin.py: WARN (could not create module spec)")
        except Exception as exc:
            click.echo(f"  plugin.py: FAIL ({exc})")
    else:
        click.echo("  plugin.py: SKIP (file not found)")

    click.echo("Validation complete.")


@plugin.command("list")
def list_plugins() -> None:
    """List available plugins from the community registry."""
    registry_url = os.getenv(
        "PLUGIN_REGISTRY_URL",
        "https://raw.githubusercontent.com/Cerid-AI/plugin-registry/main/registry.json",
    )
    try:
        resp = httpx.get(registry_url, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        plugins = data if isinstance(data, list) else data.get("plugins", [])
    except Exception as exc:
        click.echo(f"Failed to fetch registry: {exc}", err=True)
        raise SystemExit(1)

    if not plugins:
        click.echo("No plugins found in registry.")
        return

    click.echo(f"{'Name':<30} {'Type':<12} {'Version':<10} Description")
    click.echo("-" * 80)
    for p in plugins:
        click.echo(
            f"{p.get('name', '?'):<30} "
            f"{p.get('type', '?'):<12} "
            f"{p.get('version', '?'):<10} "
            f"{p.get('description', '')[:40]}"
        )
