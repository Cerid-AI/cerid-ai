#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Emit pip-licenses-shaped JSON for a pinned lock, without installing anything.

The `license-scan` CI job used to run `pip install -r requirements.txt` purely
to let `pip-licenses` read installed metadata. That pulls torch, onnxruntime and
the rest of the ML stack — about **7 billable job-minutes per run**, on a
workflow that runs on every push to main. Reading licences does not require the
packages to exist on disk; PyPI publishes the metadata.

It is also **more correct**. The old job scanned whatever `requirements.txt`
happened to resolve to on the runner that day, which is not necessarily what
ships: the image is built from `requirements.lock`. Scanning the lock scans the
shipped set (audit finding GATE-08).

Output is the exact shape `pip-licenses --format=json` produces, so
`lint-license-denylist.py --tool pip-licenses` consumes it unchanged.

Network failures are FATAL, never silent: a package whose licence could not be
determined is reported as an error and exits non-zero. A licence gate that
degrades to "unknown, carry on" is the failure mode this whole area keeps
producing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PYPI = "https://pypi.org/pypi/{name}/{version}/json"
_PIN_RE = re.compile(r"^([A-Za-z0-9._-]+)==([^\s;\\]+)")
_TIMEOUT = 20
_RETRIES = 3


def parse_lock(path: Path) -> list[tuple[str, str]]:
    """Every `name==version` pin in a pip-compile lock, deduplicated, sorted."""
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "--")):
            continue
        m = _PIN_RE.match(line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return sorted(out.items())


def _fetch(name: str, version: str) -> dict[str, str]:
    last: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            req = urllib.request.Request(
                PYPI.format(name=name, version=version),
                headers={"User-Agent": "cerid-license-scan"},
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                info = json.load(r).get("info", {})
            break
        except Exception as exc:  # noqa: BLE001 - re-raised below if terminal
            last = exc
            if attempt == _RETRIES - 1:
                raise RuntimeError(f"{name}=={version}: {exc}") from exc
    else:  # pragma: no cover - loop always breaks or raises
        raise RuntimeError(f"{name}=={version}: {last}")

    # Emit the UNION of the License field and the Trove classifiers, joined with
    # "; " (a compound form the denylist already parses).
    #
    # Not a preference order, which is what the first draft did and it silently
    # defeated the gate: pcodedmp's `license` field is the bare token "GPL"
    # while its classifier is "GNU General Public License v3 (GPLv3)". Taking
    # the short field first produced a LESS detectable string than pip-licenses
    # reads from installed metadata, and the scanner passed a lock that
    # contained a GPL-3.0 package. Caught by running this against the pre-fix
    # lock; it would otherwise have shipped as a gate that cannot fail.
    #
    # Emitting both cannot lose information, and the denylist matches on any
    # component of a compound licence string.
    parts: list[str] = []
    lic = (info.get("license") or "").strip()
    if lic and lic.upper() not in {"UNKNOWN", "NONE"}:
        # TRUNCATE long fields, never drop them: a package that pastes the
        # full GPL text into `license` with no Trove classifier used to have
        # its only signal discarded by a hard 200-char cap and emit UNKNOWN —
        # laundering strong copyleft into a string the denylist waves through.
        # The identifying header ("GNU GENERAL PUBLIC LICENSE Version 3") sits
        # in the first line, so the truncation keeps what the scanner needs.
        parts.append(lic[:200])
    parts.extend(
        c.split("License :: ", 1)[1].replace("OSI Approved :: ", "").strip()
        for c in info.get("classifiers", [])
        if c.startswith("License :: ")
    )
    seen: set[str] = set()
    uniq = [p for p in parts if p and not (p in seen or seen.add(p))]
    return {
        "Name": info.get("name") or name,
        "Version": version,
        "License": "; ".join(uniq) if uniq else "UNKNOWN",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--jobs", type=int, default=16)
    args = ap.parse_args()

    pins = parse_lock(args.lock)
    if not pins:
        print(f"ERROR: no pinned packages found in {args.lock}", file=sys.stderr)
        return 2

    rows: list[dict[str, str]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(_fetch, n, v): (n, v) for n, v in pins}
        for fut, (n, v) in futures.items():
            try:
                rows.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))

    if errors:
        print(
            f"ERROR: could not resolve licence metadata for {len(errors)} package(s); "
            "refusing to emit a partial scan:",
            file=sys.stderr,
        )
        for e in errors[:15]:
            print(f"  {e}", file=sys.stderr)
        return 1

    rows.sort(key=lambda r: r["Name"].lower())
    args.output.write_text(json.dumps(rows, indent=1))
    print(f"resolved licences for {len(rows)} pinned packages from {args.lock}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
