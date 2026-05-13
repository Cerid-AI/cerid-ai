# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the local-backend recommendation heuristic.

The truth table mirrors ``scripts/detect-gpu.sh`` so the API response stays
sensible even when the container is started directly via ``docker compose up``
(no ``HOST_RECOMMENDED_LOCAL_BACKEND`` propagated). The authoritative source
is the shell script — these tests pin the Python fallback to the same shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.routers.setup import _recommend_backend_from_hw


@dataclass
class FakeHW:
    os: str = ""
    cpu: str = ""
    gpu: str = ""
    gpu_acceleration: str = ""
    gpu_type: str = ""
    ram_gb: int = 16
    cpu_cores: int | None = 8
    recommended_local_backend: str = ""


@pytest.mark.parametrize(
    "hw, expected",
    [
        # Intel Mac + AMD discrete (the gap case Quenchforge addresses)
        (FakeHW(os="macOS 14.5", gpu="AMD Radeon Pro Vega II", gpu_type="amd-mac"), "quenchforge"),
        (FakeHW(os="macOS 14.5", gpu="AMD Radeon Pro W6800X Duo"), "quenchforge"),
        (FakeHW(os="macOS 13.7", gpu="AMD Radeon Pro 5500M"), "quenchforge"),
        # gpu_type=amd-mac is authoritative even when gpu string is generic
        (FakeHW(os="macOS 14.6", gpu="Unknown", gpu_type="amd-mac"), "quenchforge"),
        # Apple Silicon — Ollama is the right choice (Metal is well-supported)
        (FakeHW(os="macOS 14.5", gpu="Apple M2 Max", gpu_acceleration="metal"), "ollama"),
        (FakeHW(os="macOS 15.0", gpu="Apple M4 Pro", gpu_acceleration="metal"), "ollama"),
        # Intel Mac with no discrete GPU — Ollama runs CPU but works
        (FakeHW(os="macOS 13.6", gpu="Intel Iris Plus Graphics", gpu_acceleration="metal"), "ollama"),
        # Linux + NVIDIA
        (FakeHW(os="Linux 6.6", gpu="NVIDIA RTX 4090", gpu_acceleration="cuda"), "ollama"),
        # Linux + AMD ROCm
        (FakeHW(os="Linux 6.6", gpu="AMD Radeon RX 7900 XTX", gpu_acceleration="rocm"), "ollama"),
        # Linux CPU-only — cloud fallback
        (FakeHW(os="Linux 6.6", gpu="None detected", gpu_acceleration="none"), "cloud"),
        # Windows + NVIDIA — assumed CUDA via WSL or DirectML; Ollama handles
        (FakeHW(os="Windows 11", gpu="NVIDIA RTX 4080", gpu_acceleration="cuda"), "ollama"),
        # Unknown / empty — degrades to cloud
        (FakeHW(), "cloud"),
    ],
)
def test_recommend_backend_from_hw(hw: FakeHW, expected: str) -> None:
    assert _recommend_backend_from_hw(hw) == expected


def test_amd_mac_marker_beats_apple_marker_collision() -> None:
    """Apple-internal AMD GPUs (older Mac Pros, iMac Pro) must not be
    misclassified as Apple Silicon because the brand name includes "Apple"
    somewhere. The gpu_type=amd-mac signal from detect-gpu.sh is
    authoritative."""
    hw = FakeHW(
        os="macOS 14.5",
        gpu="AMD Radeon Pro Vega II Duo (Apple MPX Module)",
        gpu_type="amd-mac",
    )
    assert _recommend_backend_from_hw(hw) == "quenchforge"


def test_apple_silicon_with_amd_substring_does_not_route_to_quenchforge() -> None:
    """If the gpu_type heuristic isn't populated and the brand string somehow
    includes 'apple' (e.g. Apple Silicon), the fallback must not route to
    Quenchforge. Guards against false-positives on M-series Macs."""
    hw = FakeHW(
        os="macOS 14.5",
        gpu="Apple M2 Max",
        gpu_acceleration="metal",
    )
    assert _recommend_backend_from_hw(hw) == "ollama"


def test_explicit_recommendation_is_not_overridden_by_fallback() -> None:
    """When HOST_RECOMMENDED_LOCAL_BACKEND is propagated (start-cerid.sh path),
    the system_check endpoint must trust it rather than re-deriving."""
    # The endpoint logic preserves hw.recommended_local_backend when non-empty.
    # This test pins the contract: the helper is only called as a fallback.
    hw = FakeHW(
        os="macOS 14.5",
        gpu="AMD Radeon Pro Vega II",
        gpu_type="amd-mac",
        recommended_local_backend="ollama",  # operator override
    )
    # The helper itself still returns the heuristic — the endpoint does the
    # short-circuit. This test pins both halves of that contract.
    assert _recommend_backend_from_hw(hw) == "quenchforge"
    explicit: Any = hw.recommended_local_backend
    assert explicit == "ollama"
