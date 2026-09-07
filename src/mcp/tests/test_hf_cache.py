# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for core.utils.hf_cache — cache-first HuggingFace file resolution.

Both the reranker and NLI loaders call ``hf_hub_download`` for a model file
that is normally already present in the local HF cache after the first
container boot. The default (network-first) resolution pays a redirected
HEAD round trip before every first use even when nothing needs downloading,
and stalls or fails outright on an offline box. ``resolve_hf_file`` must
try the cache first and only reach the network when the file is missing.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from huggingface_hub.errors import LocalEntryNotFoundError

from core.utils.hf_cache import resolve_hf_file


def test_cached_file_resolves_local_only_with_no_second_call():
    """A cached file resolves via a single local_files_only=True call."""
    calls: list[dict] = []

    def fake_hf_hub_download(**kwargs):
        calls.append(kwargs)
        return "/cache/model.onnx"

    with patch("core.utils.hf_cache.hf_hub_download", side_effect=fake_hf_hub_download):
        path = resolve_hf_file(
            "cross-encoder/ms-marco-MiniLM-L-6-v2",
            "onnx/model_quint8_avx2.onnx",
            None,
            logger=logging.getLogger("test"),
        )

    assert path == "/cache/model.onnx"
    assert len(calls) == 1
    assert calls[0]["local_files_only"] is True


def test_uncached_file_falls_back_to_one_network_call(caplog):
    """A cache miss (LocalEntryNotFoundError) falls back to exactly one
    network download, and logs one INFO line about it."""
    calls: list[dict] = []

    def fake_hf_hub_download(**kwargs):
        calls.append(kwargs)
        if kwargs.get("local_files_only"):
            raise LocalEntryNotFoundError("not cached")
        return "/cache/model.onnx"

    with patch("core.utils.hf_cache.hf_hub_download", side_effect=fake_hf_hub_download):
        with caplog.at_level(logging.INFO):
            path = resolve_hf_file(
                "cross-encoder/ms-marco-MiniLM-L-6-v2",
                "onnx/model_quint8_avx2.onnx",
                None,
                logger=logging.getLogger("test"),
            )

    assert path == "/cache/model.onnx"
    assert len(calls) == 2
    assert calls[0]["local_files_only"] is True
    assert not calls[1].get("local_files_only")
    assert any("downloading" in rec.message.lower() for rec in caplog.records)


def test_non_missing_error_is_not_swallowed():
    """An unrelated exception during the local_files_only attempt propagates
    instead of triggering a network fallback."""
    def fake_hf_hub_download(**kwargs):
        raise ValueError("boom")

    with patch("core.utils.hf_cache.hf_hub_download", side_effect=fake_hf_hub_download):
        with pytest.raises(ValueError):
            resolve_hf_file(
                "cross-encoder/ms-marco-MiniLM-L-6-v2",
                "onnx/model_quint8_avx2.onnx",
                None,
                logger=logging.getLogger("test"),
            )
