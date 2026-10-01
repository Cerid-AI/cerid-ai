# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Name the server behind the Ollama-compatible port from what it reports.

``INTERNAL_LLM_PROVIDER=ollama`` says which API to speak, not which server
answers. The MLX server and Quenchforge speak the same API on the same port,
so the provider id is not a name to show anyone.
"""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx

NEUTRAL_NAME = "Local model server"
_TIMEOUT_S = 1.0


def local_server_name(version: str | None, landing: Any) -> str:
    """``version`` is ``/api/version``'s value, ``landing`` the body of ``/``."""
    if version and version.startswith("cerid-mlx"):
        return "MLX server"
    if isinstance(landing, dict) and landing.get("service") == "quenchforge":
        return "Quenchforge"
    if isinstance(landing, str) and landing.strip().startswith("Ollama is running"):
        return "Ollama"
    return NEUTRAL_NAME


def _version(resp: httpx.Response) -> str | None:
    if resp.status_code != HTTPStatus.OK:
        return None
    value = resp.json().get("version")
    return value if isinstance(value, str) and value else None


def _landing(resp: httpx.Response) -> Any:
    if resp.status_code != HTTPStatus.OK:
        return None
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _identity(version: str | None, landing: Any) -> tuple[str, str | None]:
    name = local_server_name(version, landing)
    if version is None and isinstance(landing, dict) and name != NEUTRAL_NAME:
        version = landing.get("version")
    return name, version if name != NEUTRAL_NAME else None


def identify_local_server(base_url: str) -> tuple[str, str | None]:
    """``(name, version)`` of the server at *base_url*. Raises on transport failure."""
    version = _version(httpx.get(f"{base_url}/api/version", timeout=_TIMEOUT_S))
    if local_server_name(version, None) != NEUTRAL_NAME:
        return _identity(version, None)
    return _identity(version, _landing(httpx.get(f"{base_url}/", timeout=_TIMEOUT_S)))


async def identify_local_server_async(
    client: httpx.AsyncClient, base_url: str,
) -> tuple[str, str | None]:
    """Async counterpart of :func:`identify_local_server`."""
    version = _version(await client.get(f"{base_url}/api/version"))
    if local_server_name(version, None) != NEUTRAL_NAME:
        return _identity(version, None)
    return _identity(version, _landing(await client.get(f"{base_url}/")))
