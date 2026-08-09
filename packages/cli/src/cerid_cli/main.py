# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cerid AI CLI entry point."""
from __future__ import annotations

import click

from cerid_cli.plugin import plugin


@click.group()
@click.version_option(version="0.1.0", prog_name="cerid")
def cli() -> None:
    """Cerid AI CLI — plugin management, scaffolding, and configuration."""


@cli.group()
def config() -> None:
    """Configuration management commands."""


@config.command("show")
def config_show() -> None:
    """Show current Cerid AI configuration."""
    click.echo("Configuration display not yet implemented.")


cli.add_command(plugin)


if __name__ == "__main__":
    cli()
