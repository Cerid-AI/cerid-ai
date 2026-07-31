#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every hardcoded container_name in the base compose needs a CI override.

The live-stack CI jobs (`preservation`, `benchmark-slo`) run on a self-hosted
runner that shares ONE Docker daemon with the dev box. docker-compose.yml pins
`container_name:` on every service, so without a per-service override in
docker-compose.ci.yml the CI stack's `up` collides with the dev stack's
container of the same name. The documented failure modes are both bad: it either
clobbers the dev stack, or silently *hijacks* it and the gate passes without
ever booting its own stack.

This gate exists because adding one service re-broke it. The `memory-worker`
service landed on 2026-07-30 with a hardcoded container_name and no CI override;
the next live-stack run failed with "Stack did not reach /health/ping within
180s" and no test ran at all. tasks/lessons.md called this out in advance —
"fixing them one at a time is whack-a-mole; each missing one is a fresh
failure" — so it is now checked rather than remembered.

Usage:
    python scripts/lint-ci-compose-namespacing.py [--check]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = REPO / "docker-compose.yml"
CI = REPO / "docker-compose.ci.yml"

def _services_with_container_name(path: Path) -> tuple[dict[str, str], set[str]]:
    """Return (service -> container_name, profile-gated services).

    Parsed structurally by indentation rather than with PyYAML: this runs in the
    `lint` job before any dependency install, and the shape here is simple and
    stable.

    Profile-gated services (``profiles: [...]``) never start unless a job passes
    ``--profile``; none do, so they cannot collide and are exempt.
    """
    out: dict[str, str] = {}
    gated: set[str] = set()
    service: str | None = None
    in_services = False

    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if re.match(r"^services:\s*$", raw):
            in_services = True
            continue
        if re.match(r"^[a-zA-Z_]", raw):  # another top-level key
            in_services = False
            service = None
            continue
        if not in_services:
            continue
        m = re.match(r"^  ([a-zA-Z0-9._-]+):\s*$", raw)
        if m:
            service = m.group(1)
            continue
        m = re.match(r"^\s+container_name:\s*(\S+)", raw)
        if m and service:
            out[service] = m.group(1)
            continue
        if re.match(r"^\s+profiles:", raw) and service:
            gated.add(service)
    return out, gated


def main(argv: list[str]) -> int:
    if not BASE.exists() or not CI.exists():
        print("lint-ci-compose-namespacing: compose files not found — skipped")
        return 0

    base, gated = _services_with_container_name(BASE)
    ci, _ = _services_with_container_name(CI)

    missing = sorted(set(base) - set(ci) - gated)
    unsuffixed = sorted(
        svc for svc, name in ci.items() if not name.endswith("-ci")
    )

    if missing or unsuffixed:
        print(
            "lint-ci-compose-namespacing: the CI stack shares a Docker daemon "
            "with the dev stack — these will collide on `up`:",
            file=sys.stderr,
        )
        for svc in missing:
            print(
                f"  {svc}: pins container_name {base[svc]!r} in "
                f"docker-compose.yml with no override in docker-compose.ci.yml",
                file=sys.stderr,
            )
        for svc in unsuffixed:
            print(
                f"  {svc}: CI override {ci[svc]!r} does not end in '-ci'",
                file=sys.stderr,
            )
        print(
            "\nAdd to docker-compose.ci.yml:\n"
            "  <service>:\n"
            "    container_name: <base-name>-ci\n"
            "    networks:\n"
            "      llm-network:\n"
            "        aliases:\n"
            "          - <base-name>\n"
            "The alias keeps the canonical hostname resolvable inside the CI "
            "network. See tasks/lessons.md and docs/RUNBOOK_INCIDENTS.md.",
            file=sys.stderr,
        )
        return 1

    print(
        f"lint-ci-compose-namespacing: OK — all {len(base)} pinned "
        "container_name(s) have a '-ci' override."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
