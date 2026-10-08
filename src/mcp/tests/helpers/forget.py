# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Point the forget engine at a temp dir.

``config.SYNC_DIR`` defaults to the operator's real Dropbox folder, so a test
that reaches the engine without this would append to the live registry.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.forget.registry import Registry


def isolate_forget(monkeypatch: pytest.MonkeyPatch, sync_dir: Path) -> Registry:
    """Registry, receipts and applied ledger under *sync_dir*; audit records off."""
    from app.routers import user_state
    from app.services.forget import engine

    reg = Registry(sync_dir / "forget", "m1")
    monkeypatch.setattr("config.SYNC_DIR", str(sync_dir))
    monkeypatch.setattr(engine, "get_registry", lambda: reg)
    monkeypatch.setattr(user_state, "get_registry", lambda: reg)
    monkeypatch.setattr("core.forget.registry.get_registry", lambda: reg)
    monkeypatch.setattr(engine, "_applied_path", lambda: sync_dir / "forget_applied.jsonl")
    monkeypatch.setattr(engine, "_audit", lambda *args, **kwargs: None)
    return reg
