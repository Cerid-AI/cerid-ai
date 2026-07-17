# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""AF-033 regression: app.agents.decomposer must read ``config.DOMAINS`` live.

POST /taxonomy/domain (and boot rehydration) REASSIGNS ``config.DOMAINS`` to a
new list object. A module-level ``from config import DOMAINS`` binding would
keep pointing at the stale list, so a runtime-added domain was invisible to the
decomposer until a process restart. These tests pin the live read.
"""
from __future__ import annotations


def test_get_adjacent_domains_reads_config_domains_live(monkeypatch):
    import config
    from app.agents.decomposer import _get_adjacent_domains

    # A not-yet-registered domain is absent from the adjacency map.
    assert "af033_runtime_dom" not in _get_adjacent_domains(["code"])

    # Simulate a runtime domain add — reassign config.DOMAINS (as taxonomy.py
    # and domain_rehydration.py both do).
    monkeypatch.setattr(
        config, "DOMAINS", [*config.DOMAINS, "af033_runtime_dom"], raising=False
    )

    adjacent = _get_adjacent_domains(["code"])
    assert "af033_runtime_dom" in adjacent
    assert adjacent["af033_runtime_dom"] == config.CROSS_DOMAIN_DEFAULT_AFFINITY
