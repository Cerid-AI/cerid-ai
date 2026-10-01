# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The MCP image's lock must carry CPU-only torch, never the CUDA stack.

PyPI's linux torch pulls nvidia-* and triton, which made the mcp-server image
10.5 GB and every rebuild cost ~10 GB of disk on a host whose containers never
see a GPU (audit F333). requirements.txt points torch at PyTorch's CPU index.
That holds only while the index line survives and the CPU index keeps up with
PyPI: drop the line, or let PyPI publish a torch the CPU index lacks, and a
re-resolve quietly brings ~3.8 GB of CUDA wheels back. lock-sync would accept
that lock, since it only checks the lock against requirements.txt.
"""
from __future__ import annotations

import re
from pathlib import Path

LOCK = Path(__file__).resolve().parents[2] / "src" / "mcp" / "requirements.lock"
_PIN = re.compile(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==(\S+)", re.MULTILINE)


def _pins() -> dict[str, str]:
    return {m.group(1).lower(): m.group(2) for m in _PIN.finditer(LOCK.read_text())}


def test_no_cuda_wheels_in_lock() -> None:
    cuda = sorted(
        name for name in _pins()
        if name.startswith(("nvidia-", "cuda-")) or name == "triton"
    )
    assert not cuda, f"CUDA packages in requirements.lock: {cuda}"


def test_torch_family_is_the_cpu_build() -> None:
    pins = _pins()
    for name in ("torch", "torchaudio"):
        assert pins.get(name, "").endswith("+cpu"), (
            f"{name}=={pins.get(name)} is not the +cpu build from download.pytorch.org/whl/cpu"
        )
