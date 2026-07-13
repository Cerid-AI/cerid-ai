# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""/setup/configure already-configured guard + server-side onboarding flag.

Beta triage 2026-07-12 P0-B4: a beta user's fresh browser re-entered the
first-run wizard on a configured instance and the wizard's Apply silently
rewrote live env config (ARCHIVE_PATH / CERID_LIGHTWEIGHT / WATCH_FOLDER /
OLLAMA_*). Two contracts pin the fix:

* ``POST /setup/configure`` on a configured instance responds **409** and
  changes nothing unless the request carries ``force=true``.
* Onboarding completion is persisted **server-side**
  (``POST /setup/onboarding-complete`` → ``CERID_ONBOARDING_COMPLETE`` in the
  env file, surfaced via ``GET /setup/status.onboarding_complete``) so the
  GUI no longer trusts localStorage alone.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_ORIGINAL_ENV_CONTENT = "# pre-existing\nARCHIVE_PATH=/original/archive\n"

# Every env var the configure endpoint may inject into os.environ during a
# forced apply — snapshot/restored around each test so nothing leaks.
_TOUCHED_ENV_KEYS = (
    "OPENROUTER_API_KEY",
    "ARCHIVE_PATH",
    "CERID_LIGHTWEIGHT",
    "WATCH_FOLDER",
    "OLLAMA_ENABLED",
    "OLLAMA_DEFAULT_MODEL",
    "CERID_ONBOARDING_COMPLETE",
)


@pytest.fixture(autouse=True)
def _env_snapshot():
    saved = {k: os.environ.get(k) for k in _TOUCHED_ENV_KEYS}
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture()
def setup_ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Reloaded setup router wired to a temp env file, with the warmup and
    service probes stubbed out (no network / stores in unit tests)."""
    env_file = tmp_path / ".env"
    env_file.write_text(_ORIGINAL_ENV_CONTENT, encoding="utf-8")
    monkeypatch.setenv("CERID_ENV_FILE", str(env_file))
    monkeypatch.delenv("CERID_ONBOARDING_COMPLETE", raising=False)

    sys.modules.pop("app.routers.setup", None)
    setup = importlib.import_module("app.routers.setup")

    async def _noop_warmup() -> None:
        return None

    async def _no_services() -> dict[str, str]:
        return {}

    monkeypatch.setattr(setup, "_post_configure_warmup", _noop_warmup)
    monkeypatch.setattr(setup, "_service_statuses", _no_services)

    app = FastAPI()
    app.include_router(setup.router)
    return SimpleNamespace(env_file=env_file, module=setup, client=TestClient(app))


# ---------------------------------------------------------------------------
# /setup/configure — already-configured guard
# ---------------------------------------------------------------------------


class TestConfigureGuard:
    def test_configured_instance_without_force_is_409_and_untouched(
        self, setup_ctx: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-live")  # pragma: allowlist secret

        resp = setup_ctx.client.post(
            "/setup/configure",
            json={"archive_path": "/tmp/clobber", "lightweight_mode": True},
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "already configured" in detail
        assert "force" in detail
        assert setup_ctx.env_file.read_text(encoding="utf-8") == _ORIGINAL_ENV_CONTENT, (
            "a 409'd configure must change NOTHING in the env file"
        )
        assert os.environ.get("CERID_LIGHTWEIGHT") != "true"

    def test_configured_instance_with_force_applies(
        self, setup_ctx: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-live")  # pragma: allowlist secret
        new_archive = tmp_path / "new-archive"
        new_archive.mkdir()

        resp = setup_ctx.client.post(
            "/setup/configure",
            json={"archive_path": str(new_archive), "force": True},
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        contents = setup_ctx.env_file.read_text(encoding="utf-8")
        # _sanitize_archive_path realpath-normalises (macOS /tmp → /private/tmp)
        assert f"ARCHIVE_PATH={os.path.realpath(str(new_archive))}" in contents
        assert "ARCHIVE_PATH=/original/archive" not in contents

    def test_unconfigured_instance_needs_no_force(
        self, setup_ctx: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        resp = setup_ctx.client.post(
            "/setup/configure",
            json={"openrouter_api_key": "sk-or-v1-first-run"},  # pragma: allowlist secret
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "OPENROUTER_API_KEY=sk-or-v1-first-run" in setup_ctx.env_file.read_text(
            encoding="utf-8",
        )

    def test_force_defaults_off_in_request_model(self, setup_ctx: SimpleNamespace) -> None:
        assert setup_ctx.module.ConfigureRequest().force is False


# ---------------------------------------------------------------------------
# Server-side onboarding flag
# ---------------------------------------------------------------------------


class TestOnboardingFlag:
    def test_status_defaults_to_not_complete(self, setup_ctx: SimpleNamespace) -> None:
        resp = setup_ctx.client.get("/setup/status")
        assert resp.status_code == 200
        assert resp.json()["onboarding_complete"] is False

    def test_onboarding_complete_persists_and_surfaces_in_status(
        self, setup_ctx: SimpleNamespace,
    ) -> None:
        resp = setup_ctx.client.post("/setup/onboarding-complete")
        assert resp.status_code == 200
        assert resp.json() == {"onboarding_complete": True}

        contents = setup_ctx.env_file.read_text(encoding="utf-8")
        assert "CERID_ONBOARDING_COMPLETE=true" in contents, (
            "flag must persist in the env file so it survives a restart"
        )
        assert "ARCHIVE_PATH=/original/archive" in contents, (
            "the write must not disturb existing entries"
        )
        assert os.environ.get("CERID_ONBOARDING_COMPLETE") == "true"

        status = setup_ctx.client.get("/setup/status")
        assert status.json()["onboarding_complete"] is True

    def test_env_flag_alone_marks_complete(
        self, setup_ctx: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Boot path: after a container recreate, compose env_file feeds the
        persisted flag straight into the environment — status must honour it
        without any POST in this process's lifetime."""
        monkeypatch.setenv("CERID_ONBOARDING_COMPLETE", "true")
        resp = setup_ctx.client.get("/setup/status")
        assert resp.json()["onboarding_complete"] is True

    def test_write_failure_returns_500(
        self, setup_ctx: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _boom(updates: dict[str, str]) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(setup_ctx.module, "_update_env_file", _boom)
        resp = setup_ctx.client.post("/setup/onboarding-complete")
        assert resp.status_code == 500
        assert os.environ.get("CERID_ONBOARDING_COMPLETE") != "true"
