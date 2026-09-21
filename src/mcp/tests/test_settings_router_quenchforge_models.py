# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Clearing the Quenchforge model names through PATCH /settings (F031, F173).

``utils.quenchforge_client`` resolves the model as
``os.getenv(NAME) or getattr(config.settings, NAME, "")``. The PATCH handler
wrote only the env half, so an operator clearing the field got an empty env
var, a GET that echoed "cleared", and a dispatch that quietly kept using the
module-level value — with no way to turn the lane off. The config default
(``bge-reranker-v2-m3``) also made the client's "operator must set it" guard
unreachable, so a misconfigured lane 503'd instead of reporting unconfigured.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.settings import router

_MODEL_VARS = ("QUENCHFORGE_EMBED_MODEL", "QUENCHFORGE_RERANK_MODEL")


@pytest.fixture
def client(monkeypatch):
    """Isolate both planes the handler writes: os.environ and config.settings.

    The handler mutates os.environ directly, which monkeypatch does not undo,
    and the config planes are process-global module attributes.
    """
    import config
    import config.settings

    monkeypatch.setattr(config, "SYNC_DIR", "", raising=False)
    saved_env = {k: os.environ.get(k) for k in _MODEL_VARS}
    saved_attrs = {
        k: (getattr(config, k, None), getattr(config.settings, k, None))
        for k in _MODEL_VARS
    }
    for k in _MODEL_VARS:
        os.environ.pop(k, None)

    app = FastAPI()
    app.include_router(router)
    yield TestClient(app)

    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for k, (pkg, sub) in saved_attrs.items():
        setattr(config, k, pkg)
        setattr(config.settings, k, sub)


def _client_resolved_rerank_model() -> str:
    """Exactly what utils.quenchforge_client.quenchforge_rerank resolves."""
    from config import settings as _cfg

    return os.getenv("QUENCHFORGE_RERANK_MODEL") or getattr(_cfg, "QUENCHFORGE_RERANK_MODEL", "")


def test_clearing_rerank_model_reaches_the_dispatch_reader(client):
    import config.settings

    config.settings.QUENCHFORGE_RERANK_MODEL = "bge-reranker-v2-m3"

    r = client.patch("/settings", json={"quenchforge_rerank_model": ""})

    assert r.status_code == 200
    assert r.json()["updated"] == {"quenchforge_rerank_model": ""}
    assert _client_resolved_rerank_model() == ""


def test_clearing_embed_model_reaches_the_dispatch_reader(client):
    import config.settings

    config.settings.QUENCHFORGE_EMBED_MODEL = "nomic-embed-text-v1.5"

    r = client.patch("/settings", json={"quenchforge_embed_model": ""})

    assert r.status_code == 200
    from config import settings as _cfg
    resolved = os.getenv("QUENCHFORGE_EMBED_MODEL") or getattr(_cfg, "QUENCHFORGE_EMBED_MODEL", "")
    assert resolved == ""


def test_setting_a_rerank_model_reaches_the_dispatch_reader(client):
    import config.settings

    config.settings.QUENCHFORGE_RERANK_MODEL = ""

    r = client.patch("/settings", json={"quenchforge_rerank_model": "qwen3-reranker-4b"})

    assert r.status_code == 200
    assert os.environ["QUENCHFORGE_RERANK_MODEL"] == "qwen3-reranker-4b"
    assert _client_resolved_rerank_model() == "qwen3-reranker-4b"


def test_rerank_model_has_no_config_default():
    """Quenchforge itself defaults RerankModel to "" (rerank disabled, opt-in),
    so a cerid-side default guarantees we POST a model at a daemon that is not
    serving it — and makes the client's unconfigured guard dead code."""
    env = {k: v for k, v in os.environ.items() if k != "QUENCHFORGE_RERANK_MODEL"}
    src_mcp = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, "-c",
         "import config.settings as s; print(repr(s.QUENCHFORGE_RERANK_MODEL))"],
        env=env, cwd=str(src_mcp), capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip().splitlines()[-1] == "''"
