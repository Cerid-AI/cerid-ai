# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The live-stack CI jobs boot docker-compose.yml + docker-compose.ci.yml on a
runner that may have 2 CPUs (GitHub's ubuntu runners). A service whose CPU
limit is above that never starts: "range of CPUs is from 0.01 to 2.00". The
2026-09-30 nightly SLO job failed that way after mcp-server gained a 4.0
default, because the override fitted neo4j alone.

Every service is checked, so a new limit cannot slip past one entry.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent.parent
RUNNER_CPUS = 2.0

_DEFAULTED = re.compile(r"^\$\{[A-Z0-9_]+:-([0-9.]+)\}$")


def _cpus(service: dict) -> float | None:
    raw = (((service or {}).get("deploy") or {}).get("resources") or {}).get("limits", {}).get("cpus")
    if raw is None:
        return None
    text = str(raw).strip()
    match = _DEFAULTED.match(text)
    return float(match.group(1) if match else text)


def test_every_ci_service_fits_a_two_cpu_runner() -> None:
    base = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]
    override = yaml.safe_load((REPO / "docker-compose.ci.yml").read_text())["services"]

    too_big = {}
    for name, service in base.items():
        if (service or {}).get("profiles"):
            continue  # not started by the CI jobs' `compose up`
        limit = _cpus(override.get(name, {})) or _cpus(service)
        if limit is not None and limit > RUNNER_CPUS:
            too_big[name] = limit

    assert not too_big, f"CPU limits above a {RUNNER_CPUS}-CPU runner: {too_big}"
