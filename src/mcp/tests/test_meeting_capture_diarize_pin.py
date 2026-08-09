# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The diarization checkpoint must be revision-pinned.

This is the code half of the pip-audit ignore for PYSEC-2026-3624 (lightning
RCE via a crafted checkpoint). That ignore is only honest while the checkpoint
we hand to pyannote is immutable — an unpinned model name resolves to whatever
the upstream default branch points at today.

`_load_pipeline` is not covered by the meeting-capture tests: they mock the
whole `diarize` module, so nothing ever reached `Pipeline.from_pretrained`.
"""
from __future__ import annotations

import re
import sys
import types
from unittest.mock import MagicMock

import pytest

from plugins.meeting_capture import diarize


@pytest.fixture
def fake_pyannote(monkeypatch):
    """Stand in for pyannote.audio so the loader runs without torch or HF.

    Both the parent package and the submodule go into sys.modules: the
    `from pyannote.audio import Pipeline` inside the function imports the parent
    first, and on a machine without pyannote installed that would fail before
    the submodule stub was ever consulted.
    """
    pipeline_cls = MagicMock()
    pipeline_cls.from_pretrained.return_value = object()

    parent = types.ModuleType("pyannote")
    audio = types.ModuleType("pyannote.audio")
    audio.Pipeline = pipeline_cls  # type: ignore[attr-defined]
    parent.audio = audio  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "pyannote", parent)
    monkeypatch.setitem(sys.modules, "pyannote.audio", audio)
    monkeypatch.setattr(diarize, "_pipeline_cache", None)
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    return pipeline_cls


def test_checkpoint_carries_an_immutable_revision(fake_pyannote):
    diarize._load_pipeline()

    (checkpoint,), _kwargs = fake_pyannote.from_pretrained.call_args
    model, sep, revision = checkpoint.partition("@")

    assert sep == "@", f"checkpoint is not revision-pinned: {checkpoint!r}"
    assert model == "pyannote/speaker-diarization-3.1"
    # A 40-hex commit sha, not a branch or tag — a tag can be moved, which would
    # silently restore the mutable-pointer problem this pin exists to remove.
    assert re.fullmatch(r"[0-9a-f]{40}", revision), (
        f"revision must be a full commit sha, got {revision!r}"
    )


def test_pipeline_is_cached_across_calls(fake_pyannote):
    first = diarize._load_pipeline()
    second = diarize._load_pipeline()

    assert first is second
    assert fake_pyannote.from_pretrained.call_count == 1


def test_missing_token_raises_before_touching_the_network(fake_pyannote, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="HF_TOKEN"):
        diarize._load_pipeline()

    fake_pyannote.from_pretrained.assert_not_called()
