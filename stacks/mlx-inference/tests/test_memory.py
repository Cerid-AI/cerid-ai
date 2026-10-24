# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The heavy slot's fit rule on fake readings, and the host reading itself."""

from __future__ import annotations

import subprocess
import sys

import pytest
import serve

GB = serve.GB
GPT_OSS = 65 * GB


@pytest.mark.parametrize(
    ("available", "pressure", "fits"),
    [
        (200 * GB, "normal", True),
        (73 * GB, "normal", True),  # exactly weights + margin
        (72.9 * GB, "normal", False),
        (200 * GB, "warn", False),
        (200 * GB, "critical", False),
        (None, "normal", True),  # no reading: the guard steps aside
        (None, None, True),
        (200 * GB, None, True),
        (10 * GB, None, False),
        (None, "warn", False),
    ],
)
def test_fit_rule(available, pressure, fits):
    short = serve.heavy_shortfall(GPT_OSS, {"available": available, "pressure": pressure}, 8)
    assert (short is None) is fits


def test_shortfall_reports_what_was_needed_and_seen():
    short = serve.heavy_shortfall(GPT_OSS, {"available": 41_234_567_890, "pressure": "warn"}, 8)
    assert short == {"need_gb": 73.0, "available_gb": 41.2, "pressure": "warn"}
    assert serve.heavy_shortfall(GPT_OSS, {"available": None, "pressure": "critical"}, 0)["available_gb"] is None


def test_margin_moves_the_line():
    probe = {"available": 70 * GB, "pressure": "normal"}
    assert serve.heavy_shortfall(GPT_OSS, probe, 4) is None
    assert serve.heavy_shortfall(GPT_OSS, probe, 8) is not None


def test_deferred_message_names_the_numbers():
    exc = serve.HeavyDeferred("gpt-oss-120b", {"need_gb": 73.0, "available_gb": 41.2, "pressure": "warn"})
    assert str(exc) == "heavy deferred: memory (gpt-oss-120b needs 73.0 GB, 41.2 GB available, pressure warn)"


@pytest.mark.skipif(sys.platform != "darwin", reason="reads the macOS kernel")
def test_probe_reads_this_host():
    probe = serve.memory_probe()
    assert probe["pressure"] in ("normal", "warn", "critical")
    memsize = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True).stdout)
    assert 0 < probe["available"] <= memsize


@pytest.mark.skipif(sys.platform == "darwin", reason="the non-macOS path")
def test_probe_elsewhere_has_no_reading():
    assert serve.memory_probe() == {"available": None, "pressure": None}
