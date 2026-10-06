# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Every CI step that boots the live stack pulls its public images keychain-free first.

The self-hosted Mac runners cannot open the keychain, and the Docker CLI's
default credential store on macOS is the keychain helper. The stack booted for
a year without pulling because every image was already on the runner; the
first new public image (rspamd, round 2 of the 2026-10 plan) failed both
live-stack jobs at `compose up`. The helper pulls with the keychain-free
config first. These tests hold every booting step to it, enumerating the
workflow tree rather than a remembered list of jobs.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))
HELPER = REPO / "scripts" / "ci" / "pull-public-images.sh"
UP_LINE = re.compile(r"docker compose -f docker-compose\.yml -f docker-compose\.ci\.yml up ")


def _booting_steps() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for wf in WORKFLOWS:
        text = wf.read_text(encoding="utf-8")
        # A step's `run:` block ends at the next line that is indented no
        # deeper than `run:` itself; splitting on `- name:` is close enough
        # here because every booting step is a named step.
        for step in re.split(r"\n\s*- name: ", text)[1:]:
            if UP_LINE.search(step):
                found.append((wf.name, step))
    return found


def test_the_known_live_stack_workflows_boot_through_a_step_this_test_sees():
    """The public tree has no live-stack jobs; there the set is empty and the
    rule below is vacuous. Where a known booting workflow exists, the step
    finder must see its boot, or the rule below would silently cover nothing."""
    names = {wf for wf, _ in _booting_steps()}
    for known in ("ci.yml", "slo-live-stack.yml", "nightly-eval.yml"):
        if (REPO / ".github" / "workflows" / known).exists() and "docker-compose.ci.yml" in (
            REPO / ".github" / "workflows" / known
        ).read_text(encoding="utf-8"):
            assert known in names, f"{known} boots the CI stack but no step was found"


def test_every_booting_step_pulls_public_images_first():
    for wf, step in _booting_steps():
        pull = step.find("scripts/ci/pull-public-images.sh")
        up = UP_LINE.search(step).start()
        assert pull != -1, f"{wf}: a step runs `compose up` without the keychain-free pull"
        assert pull < up, f"{wf}: the pull must run before `compose up`"


def test_helper_is_executable_and_parses():
    assert HELPER.exists()
    assert HELPER.stat().st_mode & 0o111, "helper is not executable"
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_helper_config_keeps_pulls_off_the_keychain():
    text = HELPER.read_text(encoding="utf-8")
    match = re.search(r"printf '(\{.*?\})\\n'", text)
    assert match, "the helper must write its Docker config with printf"
    config = json.loads(match.group(1).replace("%s", "PLUGINS"))
    assert config["auths"] == {"https://index.docker.io/v1/": {}}, (
        "an explicit empty Hub entry keeps lookups on the file store"
    )
    assert "credsStore" not in config and "credHelpers" not in config
    assert "DOCKER_HOST" in text, "the current context's endpoint must be carried over"
    assert "--ignore-buildable" in text
