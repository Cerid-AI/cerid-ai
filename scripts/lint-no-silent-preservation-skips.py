#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels / Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Lint: fail if any preservation test was silently skipped.

Reads a JUnit XML artifact produced by the ``preservation`` CI job and
exits non-zero if any test in the ``preservation`` testsuite was skipped
or recorded a ``preservation_skipped`` user property.

A skip on a PR build is acceptable (env vars may not be available); a skip
on a main-branch build is a regression in observability and blocks the
``docker`` gate.  This script is designed to run ONLY on ``push && main``
builds — see the ``lint-no-silent-preservation-skips`` CI job for the
``if:`` guard.

Usage
-----
    python scripts/lint-no-silent-preservation-skips.py --junit-xml path/to/results.xml

Exit codes
----------
    0  No preservation skips found — build may proceed.
    1  One or more preservation tests were skipped — details printed to stdout.
    2  The XML file could not be parsed — always fails loud.

Style note: pure stdlib only (xml.etree.ElementTree, argparse, sys) so
the script runs in a bare CI environment without extra deps.
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET  # noqa: B314 — parsing trusted CI-generated JUnit XML

_PROPERTY_NAME = "preservation_skipped"
_PRESERVATION_SUITE_KEYWORDS = ("preservation",)


def _is_preservation_suite(suite_name: str) -> bool:
    """True if the testsuite name contains a preservation keyword.

    JUnit XML produced by ``pytest -m preservation --junit-xml ...``
    uses the module path as the classname and the top-level ``testsuite``
    ``name`` attribute.  We match broadly on the keyword ``preservation``
    so this works regardless of whether the runner sets the suite name to
    the file path, the module name, or a custom label.
    """
    return any(kw in suite_name.lower() for kw in _PRESERVATION_SUITE_KEYWORDS)


def _collect_skips(tree: ET.ElementTree) -> list[str]:
    """Return a list of human-readable skip descriptions found in the XML.

    Two detection modes:

    1. ``<property name="preservation_skipped" value="..."/>`` — the
       structured record written by ``record_preservation_skip()`` in
       conftest.py.  Appears inside a ``<properties>`` block inside
       ``<testcase>``.

    2. ``<skipped .../>`` element inside a ``<testcase>`` that belongs to
       a preservation testsuite.  This catches plain ``pytest.skip()``
       calls that did NOT go through the helper (the very antipattern this
       lint job is designed to detect).
    """
    skips: list[str] = []

    root = tree.getroot()

    # Support both <testsuites><testsuite>... and bare <testsuite>... roots.
    if root.tag == "testsuites":
        suites = list(root.iter("testsuite"))
    elif root.tag == "testsuite":
        suites = [root] + list(root.iter("testsuite"))
    else:
        suites = list(root.iter("testsuite"))

    for suite in suites:
        suite_name = suite.get("name", "")

        for testcase in suite.iter("testcase"):
            test_name = testcase.get("name", "<unknown>")
            classname = testcase.get("classname", "")

            # Mode 1: structured preservation_skipped property.
            for props in testcase.iter("properties"):
                for prop in props.iter("property"):
                    if prop.get("name") == _PROPERTY_NAME:
                        value = prop.get("value", "")
                        skips.append(
                            f"  [property] {classname}::{test_name}: {value}"
                        )

            # Mode 2: <skipped> element in a preservation-tagged suite.
            # If a test has a preservation_skipped property we already
            # caught it above; avoid double-reporting by checking that
            # the property list is empty.
            has_property_skip = any(
                prop.get("name") == _PROPERTY_NAME
                for props in testcase.iter("properties")
                for prop in props.iter("property")
            )
            if has_property_skip:
                continue

            skipped_el = testcase.find("skipped")
            if skipped_el is None:
                continue

            # A <skipped> in any suite whose name or whose testcase
            # classname signals "preservation".
            is_pres_suite = _is_preservation_suite(suite_name)
            is_pres_class = _is_preservation_suite(classname)
            if is_pres_suite or is_pres_class:
                message = skipped_el.get("message", skipped_el.text or "")
                skips.append(
                    f"  [skipped] {classname}::{test_name}: {message}"
                )

    return skips


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--junit-xml",
        required=True,
        metavar="PATH",
        help="Path to the JUnit XML artifact from the preservation CI job",
    )
    args = parser.parse_args(argv)

    try:
        tree = ET.parse(args.junit_xml)  # nosec B314
    except ET.ParseError as exc:
        print(
            f"PARSE ERROR: could not read JUnit XML at {args.junit_xml!r}: {exc}",
            file=sys.stderr,
        )
        return 2
    except FileNotFoundError:
        print(
            f"FILE NOT FOUND: {args.junit_xml!r} — did the preservation job "
            "produce a JUnit artifact?",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(
            f"IO ERROR reading {args.junit_xml!r}: {exc}",
            file=sys.stderr,
        )
        return 2

    skips = _collect_skips(tree)

    if not skips:
        print(
            f"lint-no-silent-preservation-skips: OK — "
            f"no preservation skips found in {args.junit_xml}"
        )
        return 0

    print(
        f"FAIL: lint-no-silent-preservation-skips — "
        f"{len(skips)} preservation test(s) were skipped on main:\n"
    )
    for line in skips:
        print(line)
    print(
        "\nA skip in a main-branch preservation run means a capability invariant "
        "was not verified.  Either:\n"
        "  1. A required env var (OPENROUTER_API_KEY, NEO4J_PASSWORD, "
        "CERID_SYNC_DIR_HOST) is missing from the preservation CI job — add it.\n"
        "  2. The stack was unreachable — investigate the boot sequence.\n"
        "  3. A new preserve test uses bare pytest.skip() — replace with "
        "record_preservation_skip() from conftest.py."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
