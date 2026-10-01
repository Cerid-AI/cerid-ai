# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""An ONNX session asks for no more threads than the container's CPU quota."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from core.utils.cpu import onnx_intra_op_threads


@pytest.mark.parametrize(
    ("cpu_max", "threads"),
    [
        ("200000 100000", 2),   # the 2-CPU cap the Studio ran under
        ("600000 100000", 4),   # never above four
        ("150000 100000", 1),   # a fraction rounds down
        ("50000 100000", 1),    # and never to zero
    ],
)
def test_threads_follow_the_quota(tmp_path: Path, cpu_max: str, threads: int) -> None:
    f = tmp_path / "cpu.max"
    f.write_text(cpu_max + "\n")
    assert onnx_intra_op_threads(str(f)) == threads


def test_no_quota_uses_the_machine(tmp_path: Path) -> None:
    f = tmp_path / "cpu.max"
    f.write_text("max 100000\n")
    assert onnx_intra_op_threads(str(f)) == min(4, os.cpu_count() or 1)


def test_every_onnx_session_in_the_server_uses_it() -> None:
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in list((root / "core").rglob("*.py")) + list((root / "utils").rglob("*.py")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"intra_op_num_threads\s*=", line) and "onnx_intra_op_threads()" not in line:
                offenders.append(f"{path.relative_to(root)}:{n}")
    assert offenders == [], f"thread count not taken from the CPU quota: {offenders}"
