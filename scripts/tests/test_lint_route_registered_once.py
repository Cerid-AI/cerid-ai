# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Plant-fault tests for scripts/lint-route-registered-once.py (gate G-A).

Each test plants a synthetic router tree, runs the real detector over it, and
asserts the gate fires (or stays quiet). The "must not fire" cases matter as
much as the hits: a gate that flags distinct routes is worse than no gate,
because the first false positive is what gets it disabled.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lint_route_registered_once", _ROOT / "scripts" / "lint-route-registered-once.py",
)
assert _SPEC is not None and _SPEC.loader is not None
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_route_registered_once"] = lint
_SPEC.loader.exec_module(lint)


def _plant(tmp_path: Path, **modules: str) -> list["lint.Collision"]:
    routers = tmp_path / "routers"
    routers.mkdir(exist_ok=True)
    for name, source in modules.items():
        (routers / f"{name}.py").write_text(source, encoding="utf-8")
    return lint.collect_all(scan_dirs=[routers], rel_to=tmp_path)


class TestCrossModuleCollision:
    def test_same_method_and_path_in_two_modules_flagged(self, tmp_path: Path) -> None:
        """The shipped shape: health.py and plugins.py both own GET /plugins."""
        health = (
            "router = APIRouter()\n"
            "\n"
            "@router.get('/plugins')\n"
            "def plugins_endpoint():\n"
            "    return {}\n"
        )
        plugins = (
            "router = APIRouter(tags=['plugins'])\n"
            "\n"
            "@router.get('/plugins', response_model=PluginListResponse)\n"
            "def list_plugins():\n"
            "    return PluginListResponse()\n"
        )
        hits = _plant(tmp_path, health=health, plugins=plugins)
        assert len(hits) == 1
        assert hits[0].key() == "GET /plugins"
        assert {s.handler for s in hits[0].sites} == {"plugins_endpoint", "list_plugins"}

    def test_prefix_is_applied_before_comparing(self, tmp_path: Path) -> None:
        """`APIRouter(prefix='/briefs')` + `@router.get('/x')` is GET /briefs/x.

        brief_settings.py and briefs.py share the /briefs prefix, so the
        prefix has to be resolved or the gate compares the wrong strings.
        """
        a = (
            "router = APIRouter(prefix='/briefs')\n"
            "@router.get('/settings')\n"
            "def a():\n"
            "    return {}\n"
        )
        b = (
            "router = APIRouter(prefix='/briefs')\n"
            "@router.get('/settings')\n"
            "def b():\n"
            "    return {}\n"
        )
        assert [c.key() for c in _plant(tmp_path, a=a, b=b)] == ["GET /briefs/settings"]

    def test_prefix_makes_identical_decorator_paths_distinct(self, tmp_path: Path) -> None:
        a = "router = APIRouter(prefix='/analytics')\n@router.get('/summary')\ndef a():\n    return {}\n"
        b = "router = APIRouter(prefix='/digests')\n@router.get('/summary')\ndef b():\n    return {}\n"
        assert _plant(tmp_path, a=a, b=b) == []


class TestPathParameterIdentity:
    def test_differently_named_parameters_still_collide(self, tmp_path: Path) -> None:
        """Starlette matches on shape: {id} and {artifact_id} are one route."""
        a = "router = APIRouter()\n@router.get('/artifacts/{id}')\ndef a(id):\n    return {}\n"
        b = "router = APIRouter()\n@router.get('/artifacts/{artifact_id}')\ndef b(artifact_id):\n    return {}\n"
        hits = _plant(tmp_path, a=a, b=b)
        assert [c.key() for c in hits] == ["GET /artifacts/{}"]
        # The raw paths are preserved for the operator reading the failure.
        assert {s.raw_path for s in hits[0].sites} == {"/artifacts/{id}", "/artifacts/{artifact_id}"}

    def test_literal_segment_does_not_collide_with_parameter(self, tmp_path: Path) -> None:
        """`/artifacts/recent` and `/artifacts/{id}` are genuinely different
        registrations; flagging them would make the gate unusable."""
        a = "router = APIRouter()\n@router.get('/artifacts/recent')\ndef a():\n    return {}\n"
        b = "router = APIRouter()\n@router.get('/artifacts/{id}')\ndef b(id):\n    return {}\n"
        assert _plant(tmp_path, a=a, b=b) == []


class TestMustNotFire:
    def test_different_methods_on_one_path_not_flagged(self, tmp_path: Path) -> None:
        src = (
            "router = APIRouter()\n"
            "@router.get('/settings')\n"
            "def read():\n"
            "    return {}\n"
            "@router.patch('/settings')\n"
            "def write():\n"
            "    return {}\n"
        )
        assert _plant(tmp_path, settings=src) == []

    def test_non_router_dot_get_not_treated_as_a_route(self, tmp_path: Path) -> None:
        """`cache.get("/plugins")` is a dict lookup, not a registration."""
        src = (
            "router = APIRouter()\n"
            "@cache.get('/plugins')\n"
            "def a():\n"
            "    return {}\n"
            "@router.get('/plugins')\n"
            "def b():\n"
            "    return {}\n"
        )
        assert _plant(tmp_path, x=src) == []


class TestIntraModuleCollision:
    def test_two_decorators_in_one_module_flagged(self, tmp_path: Path) -> None:
        """The second shadows the first exactly as a second module would."""
        src = (
            "router = APIRouter()\n"
            "@router.post('/ingest')\n"
            "def a():\n"
            "    return {}\n"
            "@router.post('/ingest')\n"
            "def b():\n"
            "    return {}\n"
        )
        assert [c.key() for c in _plant(tmp_path, ingest=src)] == ["POST /ingest"]

    def test_two_routers_in_one_module_are_kept_apart(self, tmp_path: Path) -> None:
        """agent_console.py declares `router` and `activity_router`; both are
        included, and their prefixes must be resolved independently."""
        src = (
            "router = APIRouter(prefix='/agent-console')\n"
            "activity_router = APIRouter(prefix='/activity')\n"
            "@router.get('/runs')\n"
            "def a():\n"
            "    return {}\n"
            "@activity_router.get('/runs')\n"
            "def b():\n"
            "    return {}\n"
        )
        assert _plant(tmp_path, agent_console=src) == []


class TestAllowlist:
    def test_allowlist_parses_key_and_reason(self, tmp_path, monkeypatch) -> None:
        allow_file = tmp_path / "allow.txt"
        allow_file.write_text("# header\n\nGET /plugins  # G-A seed — two owners\n")
        monkeypatch.setattr(lint, "ALLOWLIST_PATH", allow_file)
        assert lint._load_allowlist() == {"GET /plugins": "G-A seed — two owners"}

    def test_repo_allowlist_has_a_reason_for_every_entry(self) -> None:
        """The house rule: one line, one reason. A bare key is not reviewable."""
        for key, reason in lint._load_allowlist().items():
            assert reason and not reason.startswith("TODO"), f"{key} has no reason"
