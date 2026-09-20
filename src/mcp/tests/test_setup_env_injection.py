# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""_update_env_file must not let a value forge extra .env lines (F027)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def _reload_setup(env_file: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CERID_ENV_FILE", str(env_file))
    sys.modules.pop("app.routers.setup", None)
    return importlib.import_module("app.routers.setup")


@pytest.fixture()
def setup_mod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_file = tmp_path / ".env"
    env_file.write_text("CERID_TIER=community\nOPENROUTER_API_KEY=\n", encoding="utf-8")
    mod = _reload_setup(env_file, monkeypatch)
    yield mod, env_file
    sys.modules.pop("app.routers.setup", None)


INJECTING_VALUES = [
    "sk-aaaaaaaaaaaaaaaa\nCERID_TIER=enterprise",
    "sk-aaaaaaaaaaaaaaaa\r\nCERID_API_KEY=",
    "sk-aaaaaaaaaaaaaaaa\rCERID_TIER=enterprise",
    "sk-aaaaaaaaaaaaaaaa\x00CERID_TIER=enterprise",
]


@pytest.mark.parametrize("value", INJECTING_VALUES)
def test_control_characters_in_new_key_are_refused(setup_mod, value: str):
    mod, env_file = setup_mod
    with pytest.raises(ValueError):
        mod._update_env_file({"HF_TOKEN": value})
    assert "enterprise" not in env_file.read_text(encoding="utf-8")
    assert env_file.read_text(encoding="utf-8").count("CERID_TIER=") == 1


@pytest.mark.parametrize("value", INJECTING_VALUES)
def test_control_characters_in_existing_key_are_refused(setup_mod, value: str):
    """The replace-in-place branch is the one the openrouter key takes."""
    mod, env_file = setup_mod
    with pytest.raises(ValueError):
        mod._update_env_file({"OPENROUTER_API_KEY": value})
    text = env_file.read_text(encoding="utf-8")
    assert "enterprise" not in text
    assert "CERID_API_KEY" not in text


def test_rejection_is_atomic_across_the_batch(setup_mod):
    """One bad value must not let its clean siblings land."""
    mod, env_file = setup_mod
    with pytest.raises(ValueError):
        mod._update_env_file(
            {"NEO4J_PASSWORD": "fine", "OLLAMA_DEFAULT_MODEL": "a\nCERID_TIER=enterprise"},  # pragma: allowlist secret
        )
    text = env_file.read_text(encoding="utf-8")
    assert "NEO4J_PASSWORD" not in text
    assert "enterprise" not in text


def test_ordinary_values_still_write(setup_mod):
    mod, env_file = setup_mod
    mod._update_env_file({"OPENROUTER_API_KEY": "sk-or-v1-abc", "NEO4J_PASSWORD": "p@ss w0rd"})  # pragma: allowlist secret
    text = env_file.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=sk-or-v1-abc\n" in text  # pragma: allowlist secret
    assert "NEO4J_PASSWORD=p@ss w0rd\n" in text
