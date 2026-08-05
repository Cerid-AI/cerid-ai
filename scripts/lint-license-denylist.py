#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Fail the build on GPL/AGPL/SSPL-family dependencies (Task 15).

Consumes the JSON output of either `pip-licenses --format=json` (python
side) or `license-checker --json` (npm side) and flags any package whose
license cannot be satisfied without accepting denylisted copyleft terms.

Why a custom checker instead of each tool's own `--fail-on`/`--failOn`:
`pip-licenses --fail-on` does an *exact string* match against whatever
license string the package's own metadata produced, which is
inconsistently either an SPDX identifier ("Apache-2.0") or Trove-classifier
prose ("GNU General Public License v3 (GPLv3)") depending on how the
upstream package declares its license -- verified empirically against
this repo's own dependency tree (2026-08, `pip-licenses --format=json`).
An exact-match denylist against `["GPL-2.0-only", "GPL-3.0-only"]` (the
prior ad-hoc gate this replaces) would silently never fire against a
classifier-style package. This script instead pattern-matches against
both string shapes, and treats compound "license A; license B" /
"A OR B" / "A AND B" strings correctly (see `_split_alternatives` /
`_unit_denied`) rather than naively substring-matching the whole string,
which would false-positive on a package like `docutils` that is
triple-licensed "BSD License; GNU General Public License (GPL); Public
Domain" -- GPL is merely one of three alternatives there, not a
requirement.

Denylist families and the reasoning for each (see also
scripts/license-allowlist.yaml):

  GPL-2.0 / GPL-3.0   DENIED. Strong copyleft on the combined/derivative
                       work -- incompatible with this repo's permissive
                       redistribution (Apache-2.0 today, FSL-1.1-ALv2 once
                       that transition lands).
  AGPL (any version)  DENIED. Network copyleft, stronger than plain GPL
                       (triggers on network use, not just distribution).
  SSPL                DENIED. A source-available-but-not-OSI license
                       whose copyleft is specifically engineered to
                       target proprietary/hosted competitors -- the same
                       structural threat model FSL exists to defend
                       against on the *other* side. Depending on SSPL
                       code ourselves would be self-defeating even before
                       reaching the redistribution question.
  LGPL (any version)  NOT denied. LGPL is designed for exactly the
                       dynamic/relinkable library-dependency pattern this
                       repo actually uses (see the `@img/sharp-libvips-*`
                       finding in the Task 15 report -- a native binding
                       consumed, not modified or statically linked).
                       Denylisting it would false-positive on ordinary,
                       legally uncontroversial dependency usage. Still
                       surfaced via NOTICE lines (not silently dropped)
                       so a reviewer can catch it if usage pattern ever
                       changes.
  GPL-2.0 was folded into the GPL family above (not split out) -- the
  brief asked to "consider" it, and the call is the same as GPL-3.0:
  both are non-permissive share-alike licenses incompatible with this
  repo's permissive redistribution, so both are denied under one family
  rather than two.

Usage:
    python scripts/lint-license-denylist.py --tool pip-licenses --input <path> [--label web]
    python scripts/lint-license-denylist.py --tool license-checker --input <path> [--label web]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "license-allowlist.yaml"

_GPL_PATTERNS = [
    re.compile(r"\bGPL-2\.0"),
    re.compile(r"\bGPL-3\.0"),
    re.compile(r"\bGPLv[23]\b"),
    re.compile(r"GNU General Public License"),
]
_AGPL_PATTERNS = [
    re.compile(r"\bAGPL[-\s]?[123]"),
    re.compile(r"GNU Affero General Public License"),
]
_SSPL_PATTERNS = [
    re.compile(r"\bSSPL\b"),
    re.compile(r"Server Side Public License"),
]
_LGPL_PATTERNS = [
    re.compile(r"\bLGPL"),
    re.compile(r"GNU Lesser General Public License"),
]

DENYLIST_FAMILIES: dict[str, list[re.Pattern[str]]] = {
    "GPL-2.0/GPL-3.0": _GPL_PATTERNS,
    "AGPL": _AGPL_PATTERNS,
    "SSPL": _SSPL_PATTERNS,
}

# Known gap, same spirit as _split_alternatives' "not a full SPDX-expression
# parser" caveat below: every pattern above requires a version marker or the
# full "GNU ... Public License" phrase, so a package that self-reports a bare,
# unversioned "GPL" or "AGPL" string (non-standard -- pip-licenses/
# license-checker output never actually does this across this repo's own
# tree -- but not impossible from a hand-authored `license` field) would pass
# through undetected. Tightening the regex to also catch a bare family name
# risks new false positives (e.g. a description or URL containing the word
# "GPL" incidentally); left as a documented gap rather than guessed at.


def _family_hit(text: str) -> str | None:
    for family, patterns in DENYLIST_FAMILIES.items():
        if any(p.search(text) for p in patterns):
            return family
    return None


def _is_lgpl(text: str) -> bool:
    return any(p.search(text) for p in _LGPL_PATTERNS)


def _split_alternatives(license_str: str) -> list[str]:
    """Split a compound license string into independent alternatives.

    Not a full SPDX-expression parser (parenthesized nesting is not
    tracked) -- deliberately simple, tuned against the actual strings
    pip-licenses/license-checker emit for this repo's tree. Top-level
    ';', ',' and ' OR ' are treated as disjunction (any one alternative
    satisfies the package); ' AND ' groups stay together as a single
    non-severable unit evaluated by `_unit_denied`.
    """
    segments: list[str] = []
    for chunk in re.split(r"[;,]", license_str):
        chunk = chunk.strip()
        if not chunk:
            continue
        segments.extend(s.strip() for s in re.split(r"\bOR\b", chunk) if s.strip())
    return segments or [license_str.strip()]


def _unit_denied(segment: str) -> tuple[bool, str | None]:
    """A single (possibly AND-conjoined) alternative. Denied if ANY
    conjunct hits the denylist -- conjunction means all parts' terms
    apply simultaneously, so there is no escaping one via the other."""
    parts = [p.strip() for p in re.split(r"\bAND\b", segment) if p.strip()]
    for part in parts:
        family = _family_hit(part)
        if family:
            return True, family
    return False, None


def evaluate(license_str: str) -> tuple[bool, str | None, bool]:
    """Returns (denied, family, is_lgpl_notice)."""
    if not license_str or license_str.strip().upper() == "UNKNOWN":
        return False, None, False
    alternatives = _split_alternatives(license_str)
    results = [_unit_denied(a) for a in alternatives]
    denied = all(d for d, _ in results)
    family = next((f for d, f in results if d), None) if denied else None
    return denied, family, _is_lgpl(license_str)


def _load_allowlist(ecosystem: str) -> dict[str, str]:
    if not ALLOWLIST_PATH.exists():
        return {}
    import yaml  # local import: only needed when the file is present

    data = yaml.safe_load(ALLOWLIST_PATH.read_text()) or {}
    entries = data.get(ecosystem) or {}
    return {str(k).lower(): str(v) for k, v in entries.items()}


def _load_pip_licenses(path: Path) -> list[tuple[str, str]]:
    data = json.loads(path.read_text())
    return [(item["Name"], item.get("License", "UNKNOWN")) for item in data]


def _load_license_checker(path: Path) -> list[tuple[str, str]]:
    data = json.loads(path.read_text())
    out: list[tuple[str, str]] = []
    for key, item in data.items():
        name = key.rsplit("@", 1)[0]
        licenses = item.get("licenses", "UNKNOWN")
        if isinstance(licenses, list):
            licenses = "; ".join(licenses)
        out.append((name, licenses))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", choices=["pip-licenses", "license-checker"], required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    ecosystem = "python" if args.tool == "pip-licenses" else "npm"
    allowlist = _load_allowlist(ecosystem)

    if args.tool == "pip-licenses":
        packages = _load_pip_licenses(args.input)
    else:
        packages = _load_license_checker(args.input)

    label = f"[{args.label}] " if args.label else ""
    violations: list[tuple[str, str, str]] = []
    lgpl_notices: list[tuple[str, str]] = []

    for name, license_str in packages:
        denied, family, is_lgpl = evaluate(license_str)
        if is_lgpl:
            lgpl_notices.append((name, license_str))
        if not denied:
            continue
        reason = allowlist.get(name.lower())
        if reason:
            print(f"{label}ALLOWED (reviewed exception): {name} [{license_str}] -- {reason}")
            continue
        violations.append((name, license_str, family or "?"))

    for name, license_str in lgpl_notices:
        print(f"{label}NOTICE (LGPL, not denied -- see rationale in this script's docstring): {name} [{license_str}]")

    if violations:
        print(f"\n{label}LICENSE DENYLIST VIOLATIONS ({len(violations)}):", file=sys.stderr)
        for name, license_str, family in violations:
            print(f"  {name}: {license_str}  (denylisted family: {family})", file=sys.stderr)
        print(
            "\nEach entry above is either a real incompatible dependency (raise it for human "
            "review -- do not silently drop or override this gate) or a reviewed exception that "
            "belongs in scripts/license-allowlist.yaml with a justification.",
            file=sys.stderr,
        )
        return 1

    print(f"{label}license scan: {len(packages)} packages, 0 denylist violations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
