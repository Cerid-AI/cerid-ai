# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Every setting PATCH /settings saves must come back after a restart.

The test drives the real PATCH endpoint against a real settings file, puts
the process back to its pre-PATCH state (a restart), runs the startup
hydration, and reads the result through the real GET endpoint.

The payload is checked against the update model's own field list, so a
field added to the model without a value here fails the test instead of
silently going uncovered.

Settings whose right value depends on what is installed on the machine are
kept in a per-machine file outside the sync directory, which is shared
between machines. The rest are kept in the synced file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.settings as settings_router
import config
import config.features as features_mod
import core.retrieval.sparse as sparse_mod
import utils.chunker as chunker_mod
from app.main import _hydrate_settings_from_sync
from app.routers.settings import SettingsUpdateRequest, router
from core.agents.query_agent import _parent_child_enabled

_ENV_WRITTEN_BY_PATCH = (
    "RETRIEVAL_SPARSE_ENABLED",
    "RETRIEVAL_HYPE_ENABLED",
    "ENABLE_PARENT_CHILD_RETRIEVAL",
    "EMBEDDINGS_PROVIDER",
    "RERANK_PROVIDER",
    "QUENCHFORGE_EMBED_MODEL",
    "QUENCHFORGE_RERANK_MODEL",
    "INTERNAL_LLM_PROVIDER",
    "INTERNAL_LLM_MODEL",
    "OLLAMA_ENABLED",
)
_MODULES_WRITTEN_BY_PATCH = (config, config.settings, features_mod, chunker_mod, sparse_mod)

# Two values per non-boolean field; the test uses whichever differs from the
# running value, so the PATCH always changes something.
_CANDIDATES: dict[str, tuple[Any, Any]] = {
    "categorize_mode": ("manual", "smart"),
    "hallucination_threshold": (0.61, 0.62),
    "cost_sensitivity": ("low", "high"),
    "auto_inject_threshold": (0.41, 0.42),
    "auto_inject_max": (7, 8),
    "storage_mode": ("archive", "extract_only"),
    "hybrid_vector_weight": (0.33, 0.34),
    "hybrid_keyword_weight": (0.35, 0.36),
    "rerank_llm_weight": (0.37, 0.38),
    "rerank_original_weight": (0.39, 0.40),
    "pack_relevance_weight": (0.55, 0.56),
    "adaptive_retrieval_light_top_k": (4, 6),
    "query_decomposition_max_subqueries": (3, 5),
    "mmr_lambda": (0.31, 0.32),
    "late_interaction_top_n": (6, 7),
    "late_interaction_blend_weight": (0.21, 0.22),
    "semantic_cache_threshold": (0.81, 0.82),
    "rag_mode": ("always", "off"),
    "embeddings_provider": ("quenchforge", "in-process"),
    "rerank_provider": ("quenchforge", "in-process"),
    "quenchforge_embed_model": ("placeholder-embed-a", "placeholder-embed-b"),
    "quenchforge_rerank_model": ("placeholder-rerank-a", "placeholder-rerank-b"),
    "internal_llm_provider": ("ollama", "quenchforge"),
    "internal_llm_model": ("placeholder-model-a", "placeholder-model-b"),
    "hybrid_fusion_mode": ("rrf", "tri_rrf"),
    "hybrid_rrf_sparse_weight": (2.5, 3.5),
    "processor_mode": ("hybrid", "disabled"),
    "processor_monthly_cap_usd": (12.5, 13.5),
    "processor_api_cap_fallback": ("hold", "local"),
    "processor_api_threshold_tokens": (4321, 4322),
}


class _Runtime:
    """A snapshot of everything PATCH /settings writes, to put the process back."""

    def __init__(self) -> None:
        self.attrs = {
            mod: {n: getattr(mod, n) for n in dir(mod) if n.isupper()}
            for mod in _MODULES_WRITTEN_BY_PATCH
        }
        self.toggles = dict(features_mod.FEATURE_TOGGLES)
        self.env = {k: os.environ.get(k) for k in _ENV_WRITTEN_BY_PATCH}

    def restore(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for mod, attrs in self.attrs.items():
            for name in [n for n in dir(mod) if n.isupper() and n not in attrs]:
                delattr(mod, name)
            for name, value in attrs.items():
                if getattr(mod, name, None) is not value:
                    setattr(mod, name, value)
        features_mod.FEATURE_TOGGLES.clear()
        features_mod.FEATURE_TOGGLES.update(self.toggles)
        for key, value in self.env.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(config, "SYNC_DIR", str(tmp_path / "sync"))
    monkeypatch.setattr(
        config, "HOST_SETTINGS_PATH", str(tmp_path / "machine" / "host_settings.json"),
        raising=False,
    )
    # setenv first: delenv on an unset variable registers nothing to restore.
    for key in _ENV_WRITTEN_BY_PATCH:
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    snapshot = _Runtime()
    yield snapshot
    snapshot.restore(monkeypatch)


@pytest.fixture
def client(runtime: _Runtime) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _observe(client: TestClient) -> dict[str, Any]:
    """Current settings as the running process reports and uses them."""
    seen = client.get("/settings").json()
    seen["enable_hype"] = os.environ.get("RETRIEVAL_HYPE_ENABLED", "false") == "true"
    seen["enable_parent_child_retrieval"] = _parent_child_enabled()
    return seen


def _payload(baseline: dict[str, Any]) -> dict[str, Any]:
    payload = {}
    for field in SettingsUpdateRequest.model_fields:
        first, second = _CANDIDATES.get(field, (True, False))
        payload[field] = second if baseline[field] == first else first
    return payload


def _synced_file() -> Path:
    return Path(config.SYNC_DIR) / "user" / "settings.json"


def _host_file() -> Path:
    return Path(config.HOST_SETTINGS_PATH)


def _write(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings), encoding="utf-8")


def _saved_keys(path: Path) -> set[str]:
    return set(json.loads(path.read_text(encoding="utf-8"))) & set(
        SettingsUpdateRequest.model_fields
    )


def test_every_field_is_either_synced_or_per_machine() -> None:
    fields = set(SettingsUpdateRequest.model_fields)
    host = set(settings_router.HOST_SETTING_KEYS)
    synced = set(settings_router.SYNCED_SETTING_KEYS)

    assert host & synced == set(), "a field cannot be in both groups"
    assert fields - host - synced == set(), "a field is in neither group"
    assert (host | synced) - fields == set(), "a group names a field the model does not have"


def test_every_saved_setting_survives_a_restart(
    client: TestClient, runtime: _Runtime, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fields = list(SettingsUpdateRequest.model_fields)
    baseline = _observe(client)
    payload = _payload(baseline)

    saved = client.patch("/settings", json=payload)
    assert saved.status_code == 200, saved.text
    patched = _observe(client)
    assert {f: patched[f] for f in fields} == payload

    assert _saved_keys(_host_file()) == set(settings_router.HOST_SETTING_KEYS)
    assert _saved_keys(_synced_file()) == set(settings_router.SYNCED_SETTING_KEYS)

    runtime.restore(monkeypatch)
    assert _observe(client) == baseline, "the simulated restart did not reset the process"

    _hydrate_settings_from_sync()

    restored = _observe(client)
    reverted = sorted(f for f in fields if restored[f] != payload[f])
    assert reverted == []


def test_host_settings_in_the_synced_file_are_not_applied(
    client: TestClient, runtime: _Runtime,
) -> None:
    """The synced file may come from a machine with different software installed."""
    baseline = _observe(client)
    other_machine = {
        "rerank_provider": "sidecar" if baseline["rerank_provider"] != "sidecar" else "in-process",
        "embeddings_provider": "in-process",
        "internal_llm_provider": "ollama",
        "processor_mode": "hybrid",
        "rag_mode": "always",
    }
    _write(_synced_file(), other_machine)
    before = _synced_file().read_bytes()

    _hydrate_settings_from_sync()

    restored = _observe(client)
    for key in ("rerank_provider", "embeddings_provider", "internal_llm_provider", "processor_mode"):
        assert restored[key] == baseline[key], key
    assert restored["rag_mode"] == "always"
    assert _synced_file().read_bytes() == before
    assert not _host_file().exists()


def test_synced_settings_in_the_per_machine_file_are_not_applied(
    client: TestClient, runtime: _Runtime,
) -> None:
    baseline = _observe(client)
    _write(_host_file(), {"rag_mode": "always", "rerank_provider": "in-process"})

    _hydrate_settings_from_sync()

    restored = _observe(client)
    assert restored["rag_mode"] == baseline["rag_mode"]
    assert restored["rerank_provider"] == "in-process"


def test_provider_change_survives_a_restart_through_the_per_machine_file(
    client: TestClient, runtime: _Runtime, monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _observe(client)
    assert baseline["rerank_provider"] != "quenchforge"

    saved = client.patch("/settings", json={"rerank_provider": "quenchforge"})
    assert saved.status_code == 200, saved.text
    assert json.loads(_host_file().read_text(encoding="utf-8"))["rerank_provider"] == "quenchforge"
    assert not _synced_file().exists()

    runtime.restore(monkeypatch)
    assert _observe(client)["rerank_provider"] == baseline["rerank_provider"]

    _hydrate_settings_from_sync()

    assert _observe(client)["rerank_provider"] == "quenchforge"


def test_per_machine_settings_are_saved_without_a_sync_directory(
    client: TestClient, runtime: _Runtime, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SYNC_DIR", "")

    saved = client.patch("/settings", json={"rerank_provider": "quenchforge"})
    assert saved.status_code == 200, saved.text
    runtime.restore(monkeypatch)
    monkeypatch.setattr(config, "SYNC_DIR", "")

    _hydrate_settings_from_sync()

    assert _observe(client)["rerank_provider"] == "quenchforge"


def test_one_bad_saved_value_does_not_block_the_rest(
    client: TestClient, runtime: _Runtime,
) -> None:
    baseline = _observe(client)
    good = _payload(baseline)
    _write(_synced_file(), {
        "rag_mode": "not-a-mode",
        "enable_hype": "yes",
        "hybrid_fusion_mode": good["hybrid_fusion_mode"],
    })
    _write(_host_file(), {
        "processor_monthly_cap_usd": -1,
        "processor_api_threshold_tokens": good["processor_api_threshold_tokens"],
    })

    _hydrate_settings_from_sync()

    restored = _observe(client)
    assert restored["hybrid_fusion_mode"] == good["hybrid_fusion_mode"]
    assert restored["processor_api_threshold_tokens"] == good["processor_api_threshold_tokens"]
    assert restored["rag_mode"] == baseline["rag_mode"]
    assert restored["processor_monthly_cap_usd"] == baseline["processor_monthly_cap_usd"]
    assert restored["enable_hype"] is False


def test_startup_does_not_rewrite_the_settings_files(
    client: TestClient, runtime: _Runtime,
) -> None:
    payload = {"rag_mode": "always", "rerank_provider": "quenchforge"}
    assert client.patch("/settings", json=payload).status_code == 200
    before = (_synced_file().read_bytes(), _host_file().read_bytes())

    _hydrate_settings_from_sync()

    assert (_synced_file().read_bytes(), _host_file().read_bytes()) == before
