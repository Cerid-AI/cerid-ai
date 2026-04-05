# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tiered inference configuration with platform/GPU detection.

Detects the best available execution provider for ONNX models and selects
the optimal embedding/reranking backend automatically.

Provider hierarchy (best → worst):
  1. fastembed-sidecar  — native GPU via sidecar process (Metal/CUDA/ROCm)
  2. ollama             — Ollama embedding endpoint (if available)
  3. onnx-gpu           — in-process ONNX with GPU provider
  4. onnx-cpu           — in-process ONNX with CPU (Docker default)

Usage:
  from utils.inference_config import get_inference_config
  cfg = get_inference_config()
  cfg.onnx_providers   # ["CoreMLExecutionProvider", "CPUExecutionProvider"]
  cfg.provider          # "onnx-cpu"
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("ai-companion")


class Platform(Enum):
    MACOS_ARM = "macos-arm"
    MACOS_INTEL = "macos-intel"
    LINUX_X86 = "linux-x86"
    LINUX_ARM = "linux-arm"
    WINDOWS = "windows"
    UNKNOWN = "unknown"


class InferenceTier(Enum):
    OPTIMAL = "optimal"     # GPU-accelerated (sidecar or native)
    GOOD = "good"           # CPU sidecar (native, not in Docker)
    DEGRADED = "degraded"   # Docker CPU only
    UNKNOWN = "unknown"


@dataclass
class InferenceConfig:
    """Singleton holding the detected inference configuration."""

    provider: str = "onnx-cpu"
    tier: InferenceTier = InferenceTier.DEGRADED
    platform: Platform = Platform.UNKNOWN
    gpu_available: bool = False
    gpu_name: str = ""
    onnx_providers: list[str] = field(default_factory=lambda: ["CPUExecutionProvider"])
    ollama_available: bool = False
    sidecar_available: bool = False
    sidecar_url: str = ""
    embed_latency_ms: float = 0.0
    rerank_latency_ms: float = 0.0
    message: str = ""
    detected_at: float = 0.0


# Module-level singleton
_config: InferenceConfig | None = None


def get_inference_config() -> InferenceConfig:
    """Return the current inference config (detect on first call)."""
    global _config
    if _config is None:
        _config = detect_embedding_provider()
    return _config


def detect_platform() -> Platform:
    """Detect the current platform."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return Platform.MACOS_ARM
        return Platform.MACOS_INTEL
    if system == "linux":
        if machine in ("x86_64", "amd64"):
            return Platform.LINUX_X86
        if machine in ("aarch64", "arm64"):
            return Platform.LINUX_ARM
        return Platform.LINUX_X86
    if system == "windows":
        return Platform.WINDOWS
    return Platform.UNKNOWN


def _probe_gpu(plat: Platform) -> tuple[bool, str]:
    """Detect GPU availability per platform."""
    if plat == Platform.MACOS_ARM:
        # Apple Silicon always has Metal (unified memory)
        return True, "Apple Silicon (Metal)"

    if plat in (Platform.LINUX_X86, Platform.LINUX_ARM):
        # Check NVIDIA
        if shutil.which("nvidia-smi"):
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return True, result.stdout.strip().split("\n")[0]
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass
        # Check ROCm
        if shutil.which("rocm-smi"):
            try:
                result = subprocess.run(
                    ["rocm-smi", "--showproductname"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    return True, "AMD ROCm GPU"
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass

    return False, ""


def _probe_ollama() -> bool:
    """Check if Ollama is reachable."""
    ollama_url = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
    try:
        import httpx
        resp = httpx.get(f"{ollama_url}/api/tags", timeout=2)
        return resp.status_code == 200
    except Exception:
        pass
    # Fallback: try localhost
    if "host.docker.internal" in ollama_url:
        try:
            import httpx
            resp = httpx.get("http://localhost:11434/api/tags", timeout=2)
            return resp.status_code == 200
        except Exception:
            pass
    return False


def _probe_sidecar() -> tuple[bool, str]:
    """Check if the FastEmbed sidecar is running."""
    port = os.getenv("CERID_SIDECAR_PORT", "8889")
    url = os.getenv("CERID_SIDECAR_URL", f"http://localhost:{port}")
    try:
        import httpx
        resp = httpx.get(f"{url}/health", timeout=2)
        if resp.status_code == 200:
            return True, url
    except Exception:
        pass
    return False, url


def _select_onnx_providers(plat: Platform, gpu_available: bool) -> list[str]:
    """Select ONNX execution providers based on platform and GPU."""
    providers: list[str] = []

    if plat == Platform.MACOS_ARM and gpu_available:
        # CoreML is available on Apple Silicon
        try:
            import onnxruntime as ort
            available = ort.get_available_providers()
            if "CoreMLExecutionProvider" in available:
                providers.append("CoreMLExecutionProvider")
        except ImportError:
            pass

    if plat in (Platform.LINUX_X86, Platform.LINUX_ARM) and gpu_available:
        try:
            import onnxruntime as ort
            available = ort.get_available_providers()
            if "CUDAExecutionProvider" in available:
                providers.append("CUDAExecutionProvider")
            if "ROCMExecutionProvider" in available:
                providers.append("ROCMExecutionProvider")
        except ImportError:
            pass

    if plat == Platform.WINDOWS and gpu_available:
        try:
            import onnxruntime as ort
            available = ort.get_available_providers()
            if "DmlExecutionProvider" in available:
                providers.append("DmlExecutionProvider")
            if "CUDAExecutionProvider" in available:
                providers.append("CUDAExecutionProvider")
        except ImportError:
            pass

    # CPU is always the fallback
    providers.append("CPUExecutionProvider")
    return providers


def detect_embedding_provider() -> InferenceConfig:
    """Detect the best available inference provider.

    Called once at startup and cached. Can be re-called to refresh.
    """
    global _config

    plat = detect_platform()
    gpu_available, gpu_name = _probe_gpu(plat)
    onnx_providers = _select_onnx_providers(plat, gpu_available)
    ollama_available = _probe_ollama()
    sidecar_available, sidecar_url = _probe_sidecar()

    # Determine best provider + tier
    if sidecar_available:
        provider = "fastembed-sidecar"
        tier = InferenceTier.OPTIMAL if gpu_available else InferenceTier.GOOD
        message = f"Sidecar at {sidecar_url}" + (f" with {gpu_name}" if gpu_name else "")
    elif len(onnx_providers) > 1:  # Has a GPU provider + CPU fallback
        provider = "onnx-gpu"
        tier = InferenceTier.OPTIMAL
        message = f"ONNX with {onnx_providers[0]}"
    elif ollama_available:
        provider = "ollama"
        tier = InferenceTier.GOOD
        message = "Ollama available for LLM tasks"
    else:
        provider = "onnx-cpu"
        tier = InferenceTier.DEGRADED
        message = "CPU-only inference (Docker default)"

    # Allow manual override
    manual = os.getenv("INFERENCE_MODE", "").lower()
    if manual and manual != "auto":
        if manual in ("onnx-cpu", "onnx-gpu", "ollama", "fastembed-sidecar"):
            provider = manual
            message = f"Manual override: {manual}"
            logger.info("Inference provider manually set to: %s", manual)

    config = InferenceConfig(
        provider=provider,
        tier=tier,
        platform=plat,
        gpu_available=gpu_available,
        gpu_name=gpu_name,
        onnx_providers=onnx_providers,
        ollama_available=ollama_available,
        sidecar_available=sidecar_available,
        sidecar_url=sidecar_url,
        message=message,
        detected_at=time.time(),
    )

    logger.info(
        "Inference detection: provider=%s tier=%s platform=%s gpu=%s onnx=%s",
        config.provider, config.tier.value, config.platform.value,
        config.gpu_name or "none", config.onnx_providers,
    )

    _config = config
    return config


def inference_health_payload() -> dict:
    """Return inference status for the /health endpoint."""
    cfg = get_inference_config()
    return {
        "provider": cfg.provider,
        "tier": cfg.tier.value,
        "gpu": cfg.gpu_available,
        "gpu_name": cfg.gpu_name,
        "platform": cfg.platform.value,
        "onnx_providers": cfg.onnx_providers,
        "ollama_available": cfg.ollama_available,
        "sidecar_available": cfg.sidecar_available,
        "embed_latency_ms": round(cfg.embed_latency_ms, 2),
        "rerank_latency_ms": round(cfg.rerank_latency_ms, 2),
        "message": cfg.message,
    }
