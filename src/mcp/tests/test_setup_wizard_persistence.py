# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 2.5 — setup wizard env-write target persistence.

The 2026-04-22 incident (lessons.md → "Setup wizard must write to the
file compose reads"): ``setup.py::_find_env_file()`` walked up from
``/app/app/routers/setup.py`` looking for ``.env`` / ``docker-compose.yml``.
Inside the container nothing matched, so it fell through to ``/app/.env``
— a path nothing reads. Compose's ``env_file:`` points at repo-root
``.env``. The wizard silently wrote to an orphan file and the user's
key vanished on next container rebuild.

These tests pin down the persistence contract:

* When ``CERID_ENV_FILE`` is set, ``_find_env_file()`` returns exactly
  that path. The override branch is what production uses.
* The walk-up branch returns the .env in a directory that contains a
  ``docker-compose.yml`` marker, NOT a sibling that just happens to
  have a stray .env.
* End-to-end: setting ``CERID_ENV_FILE`` to a tempfile, reloading the
  module, and calling ``_update_env_file({KEY: VAL})`` lands the value
  in the file we'd actually read on next boot.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _reload_setup() -> object:
    """Force-reimport ``app.routers.setup`` so ``_ENV_FILE`` re-binds.

    ``_ENV_FILE`` is a module-level capture of ``_find_env_file()`` at
    import time — the test cannot patch it via ``monkeypatch.setenv``
    alone; the module must be re-imported under the new env.
    """
    import sys
    sys.modules.pop("app.routers.setup", None)
    return importlib.import_module("app.routers.setup")


# ---------------------------------------------------------------------------
# _find_env_file — branch coverage
# ---------------------------------------------------------------------------


class TestFindEnvFile:
    def test_cerid_env_file_override_returned_verbatim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "host-env" / ".env"
        target.parent.mkdir()
        monkeypatch.setenv("CERID_ENV_FILE", str(target))
        setup = _reload_setup()
        assert setup._find_env_file() == target

    def test_walk_up_finds_compose_marker_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When CERID_ENV_FILE is unset, walk up returns .env from the
        directory containing docker-compose.yml — even if the .env doesn't
        exist yet (the wizard CREATES it)."""
        monkeypatch.delenv("CERID_ENV_FILE", raising=False)
        # Build a fake repo: tmp/repo/docker-compose.yml + nested src tree
        repo = tmp_path / "repo"
        nested = repo / "src" / "mcp" / "app" / "routers"
        nested.mkdir(parents=True)
        (repo / "docker-compose.yml").write_text("services: {}\n")

        # The walker starts from `Path(__file__).resolve().parent` — patch
        # __file__ via a local helper module.
        setup = _reload_setup()
        fake_file = nested / "setup.py"
        fake_file.write_text("")
        monkeypatch.setattr(setup, "__file__", str(fake_file))

        result = setup._find_env_file()
        assert result == repo / ".env", (
            f"walk-up should land on repo-root .env (containing docker-compose.yml); got {result}"
        )

    def test_no_marker_falls_through_to_docker_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With neither CERID_ENV_FILE nor a discoverable marker, the
        function falls through to the documented Docker fallback."""
        monkeypatch.delenv("CERID_ENV_FILE", raising=False)
        # Walk from a directory that has no .env / docker-compose.yml above it.
        isolated = tmp_path / "lonely" / "deep" / "nested"
        isolated.mkdir(parents=True)
        setup = _reload_setup()
        fake_file = isolated / "setup.py"
        fake_file.write_text("")
        monkeypatch.setattr(setup, "__file__", str(fake_file))

        result = setup._find_env_file()
        assert result == Path("/app/.env"), (
            f"fallback should be /app/.env; got {result} — orphan-write regression risk"
        )


# ---------------------------------------------------------------------------
# End-to-end persistence — wizard write lands in the readable file
# ---------------------------------------------------------------------------


class TestUpdateEnvFilePersistence:
    def test_update_env_file_writes_to_cerid_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End-to-end: with CERID_ENV_FILE pointing at a temp file, the
        wizard's write lands there — NOT at the /app/.env orphan path that
        triggered the 2026-04-22 incident."""
        env_file = tmp_path / "host-env" / ".env"
        env_file.parent.mkdir()
        env_file.write_text("# header preserved\n")
        monkeypatch.setenv("CERID_ENV_FILE", str(env_file))

        setup = _reload_setup()
        setup._update_env_file(
            {"OPENROUTER_API_KEY": "sk-or-v1-fresh"},  # pragma: allowlist secret
        )

        contents = env_file.read_text(encoding="utf-8")
        assert "OPENROUTER_API_KEY=sk-or-v1-fresh" in contents
        assert "# header preserved" in contents, "comment lines must survive the write"

    def test_existing_key_is_replaced_not_duplicated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("OPENROUTER_API_KEY=sk-old\nOTHER=keep\n")  # pragma: allowlist secret
        monkeypatch.setenv("CERID_ENV_FILE", str(env_file))

        setup = _reload_setup()
        setup._update_env_file({"OPENROUTER_API_KEY": "sk-new"})  # pragma: allowlist secret

        contents = env_file.read_text(encoding="utf-8")
        assert "sk-new" in contents
        assert "sk-old" not in contents
        assert "OTHER=keep" in contents
        assert contents.count("OPENROUTER_API_KEY=") == 1, (
            "key must be REPLACED in place, not appended (would silently shadow)"
        )

    def test_new_key_is_appended(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=1\n")
        monkeypatch.setenv("CERID_ENV_FILE", str(env_file))

        setup = _reload_setup()
        setup._update_env_file({"NEW_KEY": "v"})

        contents = env_file.read_text(encoding="utf-8")
        assert "EXISTING=1" in contents
        assert "NEW_KEY=v" in contents
