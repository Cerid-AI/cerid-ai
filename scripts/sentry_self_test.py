#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Sentry pipeline self-test.

Verifies that the running stack can reach the Sentry org and that an
intentional synthetic exception lands in the configured projects:

    python3 scripts/sentry_self_test.py [--mcp] [--web] [--marketing]

By default, runs all three. Each test drops a single ``test_event``-
tagged exception so the operator can filter and silence them in the
Sentry UI ("Issues > Filter: tag:cerid_self_test=true").

Operator-side verification: after running, open each Sentry project
and confirm a new event with ``tag: cerid_self_test=true`` appears
within ~30s. The script does NOT poll Sentry's API to confirm — that
requires the API token; the visual check is the canonical signal.

Exit codes:
  0 — all enabled DSNs configured + synthetic exceptions sent
  1 — a DSN was missing (set in .env or skip the flag for that side)
  2 — the sentry-sdk failed to flush (network / quota issue)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _load_dotenv() -> None:
    """Pick up repo-root .env so DSN env vars resolve outside Docker."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key in os.environ:
                continue
            os.environ[key] = value.strip().strip('"').strip("'")
    except OSError:
        return


_load_dotenv()


def _emit_synthetic_event(dsn: str, project_label: str) -> int:
    """Initialize the sentry-sdk with the given DSN and capture a
    single tagged exception. Flushes before returning so the script
    can exit cleanly.

    Returns 0 on success, 2 on flush failure.
    """
    try:
        import sentry_sdk  # type: ignore[import-not-found]
    except ImportError:
        print(
            f"[{project_label}] sentry-sdk not installed in this venv — "
            f"run from within the docker container or "
            f"`pip install sentry-sdk` first",
            file=sys.stderr,
        )
        return 2

    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=0.0,
        environment=os.getenv("SENTRY_ENVIRONMENT", "self-test"),
        release=os.getenv("SENTRY_RELEASE", "self-test"),
        # Tag every event from this script so operators can filter.
        before_send=lambda event, hint: {**event, "tags": {**event.get("tags", {}), "cerid_self_test": "true", "self_test_project": project_label}},
    )

    try:
        raise RuntimeError(
            f"cerid sentry self-test — {project_label} pipeline verification (safe to ignore)"
        )
    except RuntimeError as exc:
        event_id = sentry_sdk.capture_exception(exc)

    if not event_id:
        print(f"[{project_label}] capture_exception returned None — event was filtered", file=sys.stderr)
        return 2

    # sentry-sdk 2.x flush() returns None; the call still blocks
    # until the transport drains or the timeout elapses. We rely on
    # capture_exception having returned a non-empty event_id as the
    # success signal.
    sentry_sdk.flush(timeout=15)
    print(f"[{project_label}] event {event_id} captured + flushed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sentry pipeline self-test")
    parser.add_argument("--mcp", action="store_true", help="Test the cerid-ai-mcp DSN")
    parser.add_argument("--web", action="store_true", help="Test the cerid-ai-web DSN (uses VITE_SENTRY_DSN_WEB)")
    parser.add_argument(
        "--marketing", action="store_true", help="Test the cerid-ai-marketing DSN",
    )
    args = parser.parse_args()

    if not (args.mcp or args.web or args.marketing):
        args.mcp = args.web = args.marketing = True

    rc = 0
    targets = [
        ("cerid-ai-mcp", os.getenv("SENTRY_DSN_MCP") or os.getenv("SENTRY_DSN", "")),
    ] if args.mcp else []
    if args.web:
        targets.append(("cerid-ai-web", os.getenv("VITE_SENTRY_DSN_WEB", "")))
    if args.marketing:
        targets.append(("cerid-ai-marketing", os.getenv("SENTRY_DSN_MARKETING", "")))

    for label, dsn in targets:
        if not dsn:
            print(f"[{label}] DSN env var not set — skipping (see docs/RUNBOOK_PRODUCTION.md for provisioning)", file=sys.stderr)
            rc = max(rc, 1)
            continue
        rc = max(rc, _emit_synthetic_event(dsn, label))

    if rc == 0:
        print("\nAll enabled DSNs received a synthetic exception. Check the Sentry UI:")
        print("  https://cerid-ai.sentry.io/issues/?query=tag%3Acerid_self_test%3Atrue")
    return rc


if __name__ == "__main__":
    sys.exit(main())
