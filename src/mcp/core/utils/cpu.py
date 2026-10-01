# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The CPUs this process may use, which is not the CPUs the machine has."""
from __future__ import annotations

import os
from pathlib import Path

# cgroup v2 CPU quota file: "<quota_us> <period_us>" or "max <period_us>".
CGROUP_CPU_MAX_PATH = "/sys/fs/cgroup/cpu.max"


def effective_cpu_count(cpu_max_path: str = CGROUP_CPU_MAX_PATH) -> float:
    """Return the CPU count the process can actually use.

    ``os.cpu_count()`` reports the HOST's cores even inside a CPU-quota'd
    container (observed live: ceiling 16.8 in a 2-CPU cgroup, so the load
    throttle could mathematically never engage and the container OOM'd).
    Prefer the cgroup v2 quota when readable; ``"max"`` (no quota) and any
    read/parse failure fall back to ``os.cpu_count()``.
    """
    fallback = float(os.cpu_count() or 1)
    try:
        raw = Path(cpu_max_path).read_text().strip()
    except OSError:
        return fallback
    parts = raw.split()
    if parts and parts[0] == "max":
        return fallback
    try:
        quota_raw, period_raw = parts
        quota = float(quota_raw)
        period = float(period_raw)
    except ValueError:
        return fallback
    if quota <= 0 or period <= 0:
        return fallback
    return quota / period


def onnx_intra_op_threads(cpu_max_path: str = CGROUP_CPU_MAX_PATH) -> int:
    """Threads for one ONNX session: at most 4, and never more than the quota.

    Four threads under a 2-CPU quota spend half their time throttled. Measured
    on the Mac Studio, 2026-09-27: a retrieval took about 2 s at that setting
    and 4.5 to 5 s with three in flight; with the quota at 6 it took 1.1 s and
    1.5 to 1.8 s.
    """
    return max(1, min(4, int(effective_cpu_count(cpu_max_path))))
