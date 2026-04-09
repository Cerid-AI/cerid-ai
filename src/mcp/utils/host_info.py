# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Host hardware detection helpers.

Inside Docker the OS reports ``Linux`` and psutil returns container memory
limits, not the real host hardware.  ``start-cerid.sh`` exports the actual
host values as ``HOST_*`` environment variables.  These helpers read those
env vars with sensible fallbacks so every caller gets the same behaviour.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HostHardware:
    """Snapshot of detected host hardware."""

    ram_gb: int
    os: str
    cpu: str
    cpu_cores: int | None
    gpu: str
    gpu_acceleration: str


def get_host_ram_gb() -> int:
    """Return host RAM in whole gigabytes.

    Reads ``HOST_MEMORY_GB`` (set by start-cerid.sh), falling back to
    ``psutil.virtual_memory()`` when running outside Docker, and finally
    to 16 GB if neither is available.
    """
    host_mem = os.getenv("HOST_MEMORY_GB")
    if host_mem:
        try:
            return round(float(host_mem))
        except ValueError:
            return 16

    try:
        import psutil

        return round(psutil.virtual_memory().total / (1024**3))
    except ImportError:
        return 16


def get_host_hardware() -> HostHardware:
    """Return a full hardware snapshot from HOST_* env vars."""
    cpu_cores_raw = os.getenv("HOST_CPU_CORES", "")
    return HostHardware(
        ram_gb=get_host_ram_gb(),
        os=os.getenv("HOST_OS", "") or "Linux (container)",
        cpu=os.getenv("HOST_CPU", "") or "Unknown",
        cpu_cores=int(cpu_cores_raw) if cpu_cores_raw.isdigit() else None,
        gpu=os.getenv("HOST_GPU", "") or "Unknown",
        gpu_acceleration=os.getenv("HOST_GPU_ACCEL", "") or "none",
    )
