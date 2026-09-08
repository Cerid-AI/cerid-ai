#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Turn `detect-secrets scan` output into a pass/fail verdict.

The scan is fed by ``git ls-files -z | xargs -0 detect-secrets scan``. Once
the tracked-path list outgrows xargs' command buffer (128 KiB for GNU xargs
in the CI container; the repo crossed it at 3,085 files) xargs runs the scan
more than once and the report becomes several JSON documents back to back.
``json.load`` rejects that as "Extra data", which failed the `security` job on
main while the same scan passed on macOS, whose xargs buffer is larger. Every
document is decoded and their results merged, so the verdict is the same
however many chunks xargs made.
"""
from __future__ import annotations

import json
import sys


def parse_reports(text: str) -> list[dict]:
    """Decode one or more concatenated JSON documents."""
    decoder = json.JSONDecoder()
    reports: list[dict] = []
    pos = 0
    end = len(text)
    while pos < end:
        while pos < end and text[pos].isspace():
            pos += 1
        if pos >= end:
            break
        report, pos = decoder.raw_decode(text, pos)
        reports.append(report)
    return reports


def findings(text: str) -> dict[str, list[dict]]:
    """Merge the non-empty ``results`` of every report, keyed by file."""
    merged: dict[str, list[dict]] = {}
    for report in parse_reports(text):
        for fname, hits in report.get("results", {}).items():
            if hits:
                merged.setdefault(fname, []).extend(hits)
    return merged


def main() -> int:
    text = sys.stdin.read()
    reports = parse_reports(text)
    if not reports:
        print("::error::detect-secrets produced no report")
        return 1
    secrets = findings(text)
    if secrets:
        print("::error::Potential secrets detected in:")
        for fname, hits in secrets.items():
            for hit in hits:
                print(f"  {fname}:{hit['line_number']} - {hit['type']}")
        return 1
    chunks = len(reports)
    print(f"No secrets detected ({chunks} scan chunk{'s' if chunks != 1 else ''}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
