# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The registry lists routes at the path a client calls, prefix included."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "gen_router_registry", _ROOT / "scripts" / "gen_router_registry.py",
)
assert _SPEC is not None and _SPEC.loader is not None
gen = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen)


def test_scan_file_prepends_the_router_prefix(tmp_path: Path):
    source = tmp_path / "inbox_setup.py"
    source.write_text(
        "from fastapi import APIRouter\n"
        'router = APIRouter(prefix="/inbox", tags=["inbox"])\n'
        "\n"
        '@router.get("/setup")\n'
        "async def get_setup():\n"
        "    return {}\n"
        "\n"
        '@router.post("")\n'
        "async def post_root():\n"
        "    return {}\n"
    )
    routes = gen._scan_file(source, tmp_path)
    assert [route["path"] for route in routes] == ["/inbox/setup", "/inbox"]


def test_scan_file_without_a_prefix_keeps_the_decorator_path(tmp_path: Path):
    source = tmp_path / "health.py"
    source.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "\n"
        '@router.get("/health")\n'
        "async def health():\n"
        "    return {}\n"
    )
    assert [route["path"] for route in gen._scan_file(source, tmp_path)] == ["/health"]
