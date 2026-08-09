# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 post-audit M1-1 — R1/R12 A2A private-mode response model (unit).

The integration harness under ``tests/integration/test_e1_a2a_private_persist.py``
covers the full create→persist→GET path under the preservation marker (stack-
gated). This unit file pins the response-model contract so ci-local / prepush
catches a regression without a live stack:

- R1: ``A2ATask.input`` (and output/metadata) must accept ``None`` so GET/cancel
  of an L1+ redacted Redis record does not ValidationError/500.
- R12: ``_persist_view`` redacts ``metadata`` alongside ``input``/``output``.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict = {}

    def set(self, key, value, ex=None):  # noqa: ARG002
        self.store[key] = value

    def get(self, key):
        return self.store.get(key)

    def rpush(self, key, value):
        self.store.setdefault(key, []).append(value)

    def expire(self, key, ttl):  # noqa: ARG002
        pass


def test_a2a_task_accepts_null_input_output_metadata():
    from app.routers.a2a import A2ATask

    validated = A2ATask.model_validate(
        {
            "id": "t1",
            "skill_id": "knowledge-query",
            "status": "completed",
            "input": None,
            "output": None,
            "error": None,
            "created_at": "2026-07-23T00:00:00+00:00",
            "updated_at": "2026-07-23T00:00:00+00:00",
            "metadata": None,
        }
    )
    assert validated.input is None
    assert validated.output is None
    assert validated.metadata is None


def test_a2a_task_still_requires_identity_fields():
    from app.routers.a2a import A2ATask

    with pytest.raises(ValidationError):
        A2ATask.model_validate(
            {
                "id": None,
                "skill_id": "knowledge-query",
                "status": "completed",
                "input": None,
                "output": None,
                "created_at": "2026-07-23T00:00:00+00:00",
                "updated_at": "2026-07-23T00:00:00+00:00",
            }
        )


def test_persist_view_redacts_metadata_at_l1(monkeypatch):
    """R12: metadata joins input/output on the redaction list at L1+."""
    import app.routers.a2a as a2a_mod
    import app.services.private_mode as pm_mod

    monkeypatch.setattr(pm_mod, "get_private_mode_level", lambda: 1)
    view = a2a_mod._persist_view(
        {
            "id": "t1",
            "skill_id": "knowledge-query",
            "status": "completed",
            "input": {"query": "SECRET"},
            "output": {"answer": "SECRET"},
            "metadata": {"trace_id": "SECRET_TRACE"},
            "created_at": "x",
            "updated_at": "x",
        }
    )
    assert view["input"] is None
    assert view["output"] is None
    assert view["metadata"] is None
    assert view["status"] == "completed"


def test_persist_view_keeps_payload_at_l0(monkeypatch):
    import app.routers.a2a as a2a_mod
    import app.services.private_mode as pm_mod

    monkeypatch.setattr(pm_mod, "get_private_mode_level", lambda: 0)
    task = {
        "id": "t1",
        "skill_id": "knowledge-query",
        "status": "completed",
        "input": {"query": "PUBLIC"},
        "output": {"answer": "OK"},
        "metadata": {"trace_id": "PUBLIC_TRACE"},
    }
    view = a2a_mod._persist_view(task)
    assert view["input"] == {"query": "PUBLIC"}
    assert view["metadata"] == {"trace_id": "PUBLIC_TRACE"}


@pytest.mark.asyncio
async def test_get_task_returns_redacted_record_without_validation_error(monkeypatch):
    """R1 reproduction: load a redacted durable record through get_task + A2ATask."""
    import app.routers.a2a as a2a_mod
    import app.services.private_mode as pm_mod

    fake = _FakeRedis()
    monkeypatch.setattr(a2a_mod, "get_redis", lambda: fake)
    monkeypatch.setattr(pm_mod, "get_private_mode_level", lambda: 1)

    task_id = "r1-get"
    a2a_mod._save_task(
        {
            "id": task_id,
            "skill_id": "knowledge-query",
            "status": "completed",
            "input": {"query": "SECRET"},
            "output": {"answer": "SECRET"},
            "error": None,
            "created_at": "2026-07-23T00:00:00+00:00",
            "updated_at": "2026-07-23T00:00:00+00:00",
            "metadata": {"trace_id": "SECRET"},
        }
    )
    # Confirm durable copy is redacted.
    raw = json.loads(fake.store[f"cerid:a2a:tasks:{task_id}"])
    assert raw["input"] is None
    assert raw["metadata"] is None

    loaded = await a2a_mod.get_task(task_id)
    validated = a2a_mod.A2ATask.model_validate(loaded)
    assert validated.input is None
    assert validated.output is None
    assert validated.metadata is None
    assert validated.status == "completed"
