# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Integration tests for record_preservation_skip().

Verifies that when ``record_preservation_skip`` is called inside a pytest
fixture, the test:
  1. Is reported as skipped (not failed, not passed).
  2. Fires a ``UserWarning`` with the preservation_skip message.
  3. Has a ``preservation_skipped`` user_property attached to the node,
     which will end up in JUnit XML output.

Uses pytest's ``pytester`` fixture for an isolated sub-process run so
the assertions don't depend on the live Cerid stack.
"""
from __future__ import annotations

import pytest

# Enable the pytester plugin for this module.
pytest_plugins = ["pytester"]


@pytest.fixture
def preservation_conftest(pytester):
    """Write a minimal conftest.py that exposes record_preservation_skip."""
    pytester.makeconftest(
        """
from __future__ import annotations
import warnings
import pytest


def record_preservation_skip(
    request: pytest.FixtureRequest,
    invariant_id: str,
    reason: str,
) -> None:
    message = f"preservation_skip: {invariant_id}: {reason}"
    warnings.warn(message, UserWarning, stacklevel=2)
    request.node.user_properties.append(("preservation_skipped", message))
    pytest.skip(reason)
"""
    )


def test_record_preservation_skip_marks_test_as_skipped(pytester, preservation_conftest):
    """The test using the helper is reported as 'skipped', not 'failed'."""
    pytester.makepyfile(
        """
import pytest

def test_needs_env(request):
    from conftest import record_preservation_skip
    record_preservation_skip(request, "I3", "missing OPENROUTER_API_KEY")
    # Should not reach here
    assert False, "should have been skipped"
"""
    )
    result = pytester.runpytest("-v", "-W", "always")
    result.assert_outcomes(skipped=1, failed=0, passed=0)


def test_record_preservation_skip_fires_user_warning(pytester, preservation_conftest):
    """A UserWarning with the preservation_skip message is emitted."""
    pytester.makepyfile(
        """
import warnings
import pytest

def test_needs_env(request):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from conftest import record_preservation_skip
        try:
            record_preservation_skip(request, "I6", "missing CERID_SYNC_DIR")
        except pytest.skip.Exception:
            pass
    assert len(caught) == 1, f"expected 1 warning, got {caught}"
    w = caught[0]
    assert issubclass(w.category, UserWarning), f"expected UserWarning, got {w.category}"
    assert "I6" in str(w.message), f"invariant_id 'I6' not in warning: {w.message}"
    assert "preservation_skip" in str(w.message)
"""
    )
    result = pytester.runpytest("-v", "-W", "always")
    result.assert_outcomes(passed=1)


def test_record_preservation_skip_records_user_property(pytester, preservation_conftest):
    """The preservation_skipped user_property is appended to the test node."""
    pytester.makepyfile(
        """
import pytest

def test_needs_env(request):
    from conftest import record_preservation_skip
    try:
        record_preservation_skip(request, "I8", "sync directory not configured")
    except pytest.skip.Exception:
        pass
    # After the skip is caught, check the property was appended.
    keys = [k for k, v in request.node.user_properties]
    assert "preservation_skipped" in keys, (
        f"preservation_skipped property missing; got: {request.node.user_properties}"
    )
    values = [v for k, v in request.node.user_properties if k == "preservation_skipped"]
    assert any("I8" in v for v in values), (
        f"invariant id 'I8' not found in preservation_skipped values: {values}"
    )
"""
    )
    result = pytester.runpytest("-v", "-W", "always")
    result.assert_outcomes(passed=1)


def test_record_preservation_skip_produces_junit_property(pytester, preservation_conftest, tmp_path):
    """The JUnit XML produced for a skipped preservation test contains
    the preservation_skipped property so the lint script can detect it."""
    junit_xml = tmp_path / "junit.xml"
    pytester.makepyfile(
        """
import pytest

def test_needs_env(request):
    from conftest import record_preservation_skip
    record_preservation_skip(request, "I3", "missing NEO4J_PASSWORD")
"""
    )
    result = pytester.runpytest(
        f"--junit-xml={junit_xml}", "-v", "-W", "always"
    )
    result.assert_outcomes(skipped=1)

    assert junit_xml.exists(), "JUnit XML file was not produced"
    content = junit_xml.read_text(encoding="utf-8")
    assert "preservation_skipped" in content, (
        f"preservation_skipped property missing from JUnit XML:\n{content}"
    )
    assert "I3" in content, (
        f"invariant id 'I3' missing from JUnit XML:\n{content}"
    )
