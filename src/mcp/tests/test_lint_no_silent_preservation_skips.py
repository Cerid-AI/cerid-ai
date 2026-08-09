# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Unit tests for scripts/lint-no-silent-preservation-skips.py.

Tests the four required scenarios:

1. No preservation skips → exits 0.
2. One preservation_skipped property → exits 1 with invariant id in message.
3. Multiple preservation skips → exits 1 listing all.
4. Malformed XML → exits 2 with parse-error message (fail loud, not silent-pass).

The lint script is pure stdlib so we import it directly by path.
"""
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Import the script under test by absolute path so this test works from any
# working directory, and without requiring the scripts/ dir to be a package.
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "scripts"
    / "lint-no-silent-preservation-skips.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "lint_no_silent_preservation_skips", _SCRIPT_PATH
    )
    assert spec is not None, f"Could not load spec from {_SCRIPT_PATH}"
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_script = _load_script()


def _write_xml(content: str) -> Path:
    """Write XML to a temp file and return the path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".xml", delete=False, encoding="utf-8"
    )
    f.write(content)
    f.flush()
    f.close()
    return Path(f.name)


# ---------------------------------------------------------------------------
# Scenario 1: no preservation skips → exits 0
# ---------------------------------------------------------------------------


def test_no_skips_exits_zero(capsys):
    xml = """\
<testsuites>
  <testsuite name="preservation">
    <testcase name="test_i3_round_trip" classname="tests.integration.test_preservation_i3_verification">
    </testcase>
    <testcase name="test_sdk_health" classname="tests.integration.test_preservation_i6_sdk">
    </testcase>
  </testsuite>
</testsuites>
"""
    path = _write_xml(xml)
    try:
        rc = _script.main(["--junit-xml", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert rc == 0
    captured = capsys.readouterr()
    assert "OK" in captured.out


# ---------------------------------------------------------------------------
# Scenario 2: one preservation_skipped property → exits 1 with invariant id
# ---------------------------------------------------------------------------


def test_one_property_skip_exits_one_with_invariant(capsys):
    xml = """\
<testsuites>
  <testsuite name="preservation">
    <testcase name="test_i3_verification" classname="tests.integration.test_preservation_i3">
      <properties>
        <property name="preservation_skipped"
                  value="preservation_skip: I3: missing NEO4J_PASSWORD — neo4j graph assertions require this env var"/>
      </properties>
      <skipped message="missing NEO4J_PASSWORD"/>
    </testcase>
  </testsuite>
</testsuites>
"""
    path = _write_xml(xml)
    try:
        rc = _script.main(["--junit-xml", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert rc == 1
    captured = capsys.readouterr()
    # The invariant id must appear in the output.
    assert "I3" in captured.out
    assert "FAIL" in captured.out


# ---------------------------------------------------------------------------
# Scenario 3: multiple preservation skips → exits 1 listing all
# ---------------------------------------------------------------------------


def test_multiple_skips_exits_one_listing_all(capsys):
    xml = """\
<testsuites>
  <testsuite name="preservation">
    <testcase name="test_i3_verification"
              classname="tests.integration.test_preservation_i3">
      <properties>
        <property name="preservation_skipped"
                  value="preservation_skip: I3: missing NEO4J_PASSWORD"/>
      </properties>
      <skipped message="missing NEO4J_PASSWORD"/>
    </testcase>
    <testcase name="test_list_conversations"
              classname="tests.integration.test_preservation_i8">
      <properties>
        <property name="preservation_skipped"
                  value="preservation_skip: I8: sync directory not configured"/>
      </properties>
      <skipped message="sync directory not configured"/>
    </testcase>
    <testcase name="test_stack_gate"
              classname="tests.integration.test_preservation_i6">
      <properties>
        <property name="preservation_skipped"
                  value="preservation_skip: stack: Cerid stack not reachable"/>
      </properties>
      <skipped message="Cerid stack not reachable"/>
    </testcase>
  </testsuite>
</testsuites>
"""
    path = _write_xml(xml)
    try:
        rc = _script.main(["--junit-xml", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert rc == 1
    captured = capsys.readouterr()
    assert "I3" in captured.out
    assert "I8" in captured.out
    assert "stack" in captured.out
    # Confirm count is stated (3 skips)
    assert "3" in captured.out


# ---------------------------------------------------------------------------
# Scenario 4: malformed XML → exits 2 with parse-error message
# ---------------------------------------------------------------------------


def test_malformed_xml_exits_two(capsys):
    path = _write_xml("<testsuites><broken>")
    try:
        rc = _script.main(["--junit-xml", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert rc == 2
    captured = capsys.readouterr()
    assert "PARSE ERROR" in captured.err or "PARSE ERROR" in captured.out


# ---------------------------------------------------------------------------
# Bonus: file-not-found → exits 2 (not silent-pass)
# ---------------------------------------------------------------------------


def test_missing_file_exits_two(capsys):
    rc = _script.main(["--junit-xml", "/tmp/definitely-does-not-exist-x9z.xml"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "FILE NOT FOUND" in captured.err or "FILE NOT FOUND" in captured.out


# ---------------------------------------------------------------------------
# Edge case: <skipped> without a property in a preservation suite
# (bare pytest.skip — the antipattern we want to catch too)
# ---------------------------------------------------------------------------


def test_bare_skipped_element_in_preservation_suite_exits_one(capsys):
    xml = """\
<testsuites>
  <testsuite name="preservation">
    <testcase name="test_i6_sdk_health"
              classname="tests.integration.test_preservation_i6_sdk">
      <skipped message="OPENROUTER_API_KEY not set"/>
    </testcase>
  </testsuite>
</testsuites>
"""
    path = _write_xml(xml)
    try:
        rc = _script.main(["--junit-xml", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert rc == 1
    captured = capsys.readouterr()
    assert "FAIL" in captured.out
    # The test name or message should appear.
    assert "test_i6_sdk_health" in captured.out or "OPENROUTER" in captured.out


# ---------------------------------------------------------------------------
# Edge case: bare root <testsuite> (not wrapped in <testsuites>)
# ---------------------------------------------------------------------------


def test_bare_testsuite_root_detected(capsys):
    xml = """\
<testsuite name="preservation">
  <testcase name="test_i3_round_trip"
            classname="tests.integration.test_preservation_i3">
    <properties>
      <property name="preservation_skipped"
                value="preservation_skip: I3: missing NEO4J_PASSWORD"/>
    </properties>
    <skipped message="missing NEO4J_PASSWORD"/>
  </testcase>
</testsuite>
"""
    path = _write_xml(xml)
    try:
        rc = _script.main(["--junit-xml", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert rc == 1
    captured = capsys.readouterr()
    assert "I3" in captured.out
