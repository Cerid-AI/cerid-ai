# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for core.utils.onnx_providers.resolve_providers."""

from unittest.mock import patch

from core.utils.onnx_providers import resolve_providers


class TestResolveProvidersAuto:
    """Auto-detect path (no override)."""

    def test_cpu_only_host_returns_cpu(self):
        with patch(
            "core.utils.onnx_providers.ort.get_available_providers",
            return_value=["CPUExecutionProvider"],
        ):
            assert resolve_providers("") == ["CPUExecutionProvider"]

    def test_cuda_host_picks_cuda_first(self):
        with patch(
            "core.utils.onnx_providers.ort.get_available_providers",
            return_value=["CUDAExecutionProvider", "CPUExecutionProvider"],
        ):
            result = resolve_providers("")
        assert result == ["CUDAExecutionProvider", "CPUExecutionProvider"]

    def test_apple_silicon_picks_coreml(self):
        with patch(
            "core.utils.onnx_providers.ort.get_available_providers",
            return_value=[
                "CoreMLExecutionProvider",
                "AzureExecutionProvider",
                "CPUExecutionProvider",
            ],
        ):
            result = resolve_providers("")
        assert result == ["CoreMLExecutionProvider", "CPUExecutionProvider"]

    def test_cpu_always_appended(self):
        """Even if a non-CPU provider is the only match, CPU must be present."""
        with patch(
            "core.utils.onnx_providers.ort.get_available_providers",
            return_value=["CUDAExecutionProvider"],
        ):
            result = resolve_providers("")
        assert result[-1] == "CPUExecutionProvider"
        assert "CUDAExecutionProvider" in result


class TestResolveProvidersOverride:
    """Operator override via ONNX_EXECUTION_PROVIDERS env var."""

    def test_override_respects_priority(self):
        with patch(
            "core.utils.onnx_providers.ort.get_available_providers",
            return_value=[
                "CUDAExecutionProvider",
                "CoreMLExecutionProvider",
                "CPUExecutionProvider",
            ],
        ):
            # Operator wants CoreML first even though CUDA is available.
            result = resolve_providers("CoreMLExecutionProvider,CUDAExecutionProvider")
        assert result[0] == "CoreMLExecutionProvider"
        assert result[1] == "CUDAExecutionProvider"
        assert result[-1] == "CPUExecutionProvider"

    def test_override_drops_unavailable_provider(self):
        """Unavailable providers in the override are silently dropped (with a warning)."""
        with patch(
            "core.utils.onnx_providers.ort.get_available_providers",
            return_value=["CPUExecutionProvider"],
        ):
            result = resolve_providers("CUDAExecutionProvider,CPUExecutionProvider")
        assert result == ["CPUExecutionProvider"]

    def test_override_whitespace_and_empty_entries_skipped(self):
        with patch(
            "core.utils.onnx_providers.ort.get_available_providers",
            return_value=["CPUExecutionProvider"],
        ):
            result = resolve_providers(" , CPUExecutionProvider , ")
        assert result == ["CPUExecutionProvider"]

    def test_override_dedupes(self):
        with patch(
            "core.utils.onnx_providers.ort.get_available_providers",
            return_value=["CPUExecutionProvider"],
        ):
            result = resolve_providers("CPUExecutionProvider,CPUExecutionProvider")
        assert result == ["CPUExecutionProvider"]

    def test_override_appends_cpu_if_omitted(self):
        with patch(
            "core.utils.onnx_providers.ort.get_available_providers",
            return_value=[
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ],
        ):
            result = resolve_providers("CUDAExecutionProvider")
        assert result == ["CUDAExecutionProvider", "CPUExecutionProvider"]
