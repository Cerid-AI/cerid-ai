# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""rspamd is a service of the stack, reachable from the API container by name.

It lived in stacks/rspamd as a loopback-only sidecar. Inside ai-companion-mcp
``127.0.0.1:11333`` is the container's own loopback, so every scan was refused.
The service joins the stack network as ``rspamd`` with no host port; the
overlays rename it the way they rename every other pinned container.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def _services(name: str) -> dict:
    return yaml.safe_load((REPO / name).read_text())["services"]


def test_rspamd_is_a_pinned_service_on_the_stack_network() -> None:
    rspamd = _services("docker-compose.yml")["rspamd"]
    image = rspamd["image"]
    assert image.startswith("rspamd/rspamd:") and not image.endswith(":latest")
    assert "llm-network" in rspamd["networks"]
    assert not rspamd.get("profiles"), "triage needs it; it starts with the stack"
    assert "ports" not in rspamd, "the API reaches it by name; nothing on the host needs the port"
    assert "healthcheck" in rspamd
    assert any(v.startswith("./stacks/rspamd/local.d:") and v.endswith(":ro") for v in rspamd["volumes"])


def test_rspamd_is_renamed_by_every_overlay_that_renames_the_stack() -> None:
    base_name = _services("docker-compose.yml")["rspamd"]["container_name"]
    for overlay, suffix in (("docker-compose.ci.yml", "-ci"), ("docker-compose.sandbox.yml", "-sandbox")):
        if not (REPO / overlay).exists():
            # The sandbox overlay is internal-only; this test is synced to the
            # public tree, which carries the CI overlay alone.
            continue
        service = _services(overlay)["rspamd"]
        assert service["container_name"] == base_name + suffix, overlay
        assert "rspamd" in service["networks"]["llm-network"]["aliases"], overlay


def test_the_standalone_stack_file_is_gone() -> None:
    # Two compose projects claiming cerid-rspamd would trip the launcher's
    # conflict preflight on every start.
    assert not (REPO / "stacks" / "rspamd" / "docker-compose.yml").exists()
    assert (REPO / "stacks" / "rspamd" / "local.d" / "options.inc").exists()


def test_the_launcher_knows_the_container_name() -> None:
    text = (REPO / "scripts" / "start-cerid.sh").read_text()
    fallback = next(line for line in text.splitlines() if 'REQUIRED_NAMES="' in line)
    assert "cerid-rspamd" in fallback
