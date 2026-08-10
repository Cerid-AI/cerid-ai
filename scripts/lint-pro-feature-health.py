#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Gate: no Pro feature may be entitled-but-not-loaded on a live stack.

Background (2026-08-09 Pro-feature audit): three Pro plugins —
``metamorphic_verification``, ``meeting_capture`` (three features), and
``spotlight_donor`` (since deleted; the feature is implemented desktop-side) —
failed to load on a fully licensed install for an unknown
length of time. Nothing noticed, because ``plugins/__init__.py`` logged the
failure at ERROR and discarded it: ``/health`` looked fine and
``/billing/capabilities`` kept reporting the features as enabled. The customer
was paying for five features the runtime had silently dropped.

``/health.pro_features.degraded`` now enumerates exactly that condition. This
script fails the build on a non-empty list.

A feature is NOT flagged here when it is legitimately absent from the backend:
``PLANNED_FEATURES`` (rendered "Coming in 1.0.x") and desktop-implemented
connectors report their own ``implementation`` and are excluded by the health
endpoint itself.

Usage::

    ./scripts/lint-pro-feature-health.py                 # against localhost:8888
    ./scripts/lint-pro-feature-health.py --base URL      # explicit target
    ./scripts/lint-pro-feature-health.py --require-tier pro
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = os.environ.get("CERID_PRESERVATION_MCP", "http://127.0.0.1:8888")


def fetch_health(base: str, timeout: float) -> dict:
    req = urllib.request.Request(f"{base.rstrip('/')}/health")
    api_key = os.getenv("CERID_API_KEY")
    if api_key:
        req.add_header("X-API-Key", api_key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument(
        "--require-tier",
        default="",
        help="Fail if the stack is not at least this tier (guards against a "
             "vacuous pass on a community stack, where no Pro flag is entitled "
             "and 'degraded' is trivially empty).",
    )
    args = ap.parse_args(argv)

    try:
        health = fetch_health(args.base, args.timeout)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"::error::pro-feature-health: cannot reach {args.base}/health: {exc}")
        return 2

    pro = health.get("pro_features")
    if not isinstance(pro, dict):
        # Absent block means an old build or a swallowed exception — either way
        # the gate cannot do its job, and silently passing is how this class of
        # defect survived in the first place.
        print("::error::pro-feature-health: /health has no 'pro_features' block "
              "— the gate cannot verify anything. Is the stack running this build?")
        return 2

    features = pro.get("features") or {}
    if args.require_tier:
        entitled = [f for f, e in features.items() if e.get("entitled")]
        if not entitled:
            print(f"::error::pro-feature-health: --require-tier={args.require_tier} "
                  "but no Pro feature is entitled on this stack — the check would "
                  "pass vacuously. Set CERID_TIER and restart.")
            return 2

    degraded = pro.get("degraded") or []
    if degraded:
        print(f"::error::pro-feature-health: {len(degraded)} entitled Pro "
              "feature(s) are NOT loaded — customers are paying for these:")
        for flag in degraded:
            reason = (features.get(flag) or {}).get("blocked_reason", "unknown")
            print(f"  {flag}: {reason}")
        print("\nFix the plugin load failure, or mark the feature "
              "PLANNED_FEATURES in config/features.py if it genuinely is not built.")
        return 1

    checked = sum(1 for e in features.values() if e.get("entitled"))
    print(f"pro-feature-health OK — {checked} entitled Pro feature(s), none degraded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
