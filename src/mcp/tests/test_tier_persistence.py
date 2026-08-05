# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""POST /settings/tier must persist the tier so a restart re-derives it.

Beta triage 2026-07-12 P0-B5: the tier endpoint rebound ``config.FEATURE_TIER``
in memory only, so the 22:43 OOM restart silently reset a live Pro override to
the boot default. The endpoint now writes ``CERID_TIER`` through the setup
router's env-file writer (the file compose feeds back into the container
environment via ``env_file:``), and ``config/features.py`` boots
``FEATURE_TIER = os.getenv("CERID_TIER", "community")`` — so a restart
re-derives the persisted tier.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _reload_setup():
    """Re-import app.routers.setup so its module-level _ENV_FILE re-binds
    under the CERID_ENV_FILE override (same pattern as
    test_setup_wizard_persistence)."""
    sys.modules.pop("app.routers.setup", None)
    return importlib.import_module("app.routers.setup")


def _parse_env_file(path: Path) -> dict[str, str]:
    return {
        k: v
        for k, v in (
            line.split("=", 1)
            for line in path.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        )
    }


@pytest.fixture()
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text("# existing config\nEXISTING=1\n", encoding="utf-8")
    monkeypatch.setenv("CERID_ENV_FILE", str(env_file))
    _reload_setup()
    return env_file


@pytest.fixture()
def client() -> TestClient:
    from app.routers.settings import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _restore_tier_state():
    """The endpoint mutates process-global tier state + os.environ; put both
    back so this file doesn't leak tier into the rest of the suite."""
    from config import features

    original_tier = features.current_tier()
    original_env = os.environ.get("CERID_TIER")
    yield
    if original_env is None:
        os.environ.pop("CERID_TIER", None)
    else:
        os.environ["CERID_TIER"] = original_env
    features.set_tier(original_tier)


class TestTierPersistence:
    def test_post_tier_writes_env_file_and_process_env(
        self, env_file: Path, client: TestClient,
    ) -> None:
        resp = client.post("/settings/tier", json={"tier": "pro"})
        assert resp.status_code == 200
        assert resp.json()["tier"] == "pro"

        persisted = _parse_env_file(env_file)
        assert persisted.get("CERID_TIER") == "pro", (
            "tier must land in the env file the next boot re-derives from"
        )
        assert persisted.get("EXISTING") == "1", "unrelated keys must survive"
        assert os.environ.get("CERID_TIER") == "pro"

        from config import features
        assert features.current_tier() == "pro"

    def test_invalid_tier_is_rejected_and_not_persisted(
        self, env_file: Path, client: TestClient,
    ) -> None:
        resp = client.post("/settings/tier", json={"tier": "platinum"})
        assert resp.status_code == 400
        assert "CERID_TIER" not in env_file.read_text(encoding="utf-8")

    def test_tier_survives_simulated_restart(
        self, env_file: Path, client: TestClient,
    ) -> None:
        """Simulate the OOM-restart path end-to-end: POST the override, then
        boot a fresh interpreter whose environment carries the persisted
        env-file value (what compose's ``env_file:`` does) and confirm
        config.features re-derives 'pro'."""
        resp = client.post("/settings/tier", json={"tier": "pro"})
        assert resp.status_code == 200

        persisted = _parse_env_file(env_file)
        assert persisted.get("CERID_TIER") == "pro"

        from config import features
        src_mcp = str(Path(features.__file__).resolve().parents[1])
        env = {**os.environ, "CERID_TIER": persisted["CERID_TIER"], "PYTHONPATH": src_mcp}
        result = subprocess.run(  # noqa: S603 — sys.executable with a fixed -c snippet
            [sys.executable, "-c", "import config.features as f; print(f.FEATURE_TIER)"],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().splitlines()[-1] == "pro", (
            f"fresh boot must re-derive the persisted tier; got {result.stdout!r}"
        )

    def test_env_write_failure_still_applies_tier_in_memory(
        self, env_file: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Persistence is best-effort: a read-only env file must not break the
        runtime override (the pre-fix behavior) — it only loses durability."""
        import app.routers.setup as setup_mod

        def _boom(updates: dict[str, str]) -> None:
            raise OSError("read-only file system")

        monkeypatch.setattr(setup_mod, "_update_env_file", _boom)
        resp = client.post("/settings/tier", json={"tier": "enterprise"})
        assert resp.status_code == 200
        from config import features
        assert features.current_tier() == "enterprise"
