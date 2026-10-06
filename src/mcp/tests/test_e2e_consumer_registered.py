# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The browser E2E specs send a registered consumer with the narrowest scope.

An unregistered X-Client-ID resolves to ``_default``, which the /sdk/v1 ingest
and search routes scope to ``general`` and refuse everything else with 403.
E-04 sent ``e2e-test`` and wrote ``projects``; from 1.0.8 that is a 403 and
the spec read as an ingest regression. The specs now send ``e2e-harness``,
registered here with the one domain it writes.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.services.request_policy import resolve_consumer
from config.settings import CONSUMER_REGISTRY

E2E_CLIENT_ID = "e2e-harness"
SPECS = Path(__file__).resolve().parents[3] / "tests" / "beta" / "e2e" / "tests"


def test_the_e2e_consumer_is_registered_with_only_the_general_domain():
    consumer = resolve_consumer(E2E_CLIENT_ID)
    assert consumer is not CONSUMER_REGISTRY["_default"]
    assert consumer["allowed_domains"] == ["general"]
    assert consumer["strict_domains"] is True


def test_every_client_id_a_spec_sends_is_registered():
    sent = {
        m.group(1)
        for spec in sorted(SPECS.glob("*.spec.ts"))
        for m in re.finditer(r'"X-Client-ID":\s*"([^"]+)"', spec.read_text())
    }
    assert sent, "no spec sends X-Client-ID; the regex or the specs moved"
    unregistered = sorted(cid for cid in sent if cid not in CONSUMER_REGISTRY)
    assert not unregistered, f"specs send unregistered X-Client-ID values: {unregistered}"


def test_the_e2e_consumer_writes_general_only():
    specs = {spec.name: spec.read_text() for spec in SPECS.glob("*.spec.ts")}
    for name, text in specs.items():
        if E2E_CLIENT_ID not in text:
            continue
        for m in re.finditer(r'domain:\s*"([^"]+)"', text):
            assert m.group(1) == "general", f"{name} names domain {m.group(1)!r} outside the consumer's scope"
