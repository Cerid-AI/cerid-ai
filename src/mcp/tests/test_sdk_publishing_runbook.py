# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The SDK publishing runbook must describe the release workflows as built.

An operator follows this doc once, under time pressure, with credentials
nobody else has. A runbook that describes an auth path the workflow does not
implement costs a failed release and an afternoon of chasing a fix that does
not exist.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC = (REPO_ROOT / "docs" / "SDK_PUBLISHING.md").read_text()
TS_WORKFLOW = (REPO_ROOT / ".github" / "workflows" / "release-sdk-typescript.yml").read_text()
PY_WORKFLOW = (REPO_ROOT / ".github" / "workflows" / "release-sdk-python.yml").read_text()


def test_npm_auth_is_documented_as_the_workflow_implements_it() -> None:
    """The npm job hard-fails on an empty NPM_TOKEN, so the doc cannot tell an
    operator that no long-lived token lives anywhere."""
    assert "secrets.NPM_TOKEN" in TS_WORKFLOW
    assert "NPM_TOKEN" in DOC, "the runbook must say the npm token is required"
    for claim in (
        "no long-lived `NPM_TOKEN` lives anywhere",
        "no long-lived API\ntoken lives anywhere for either registry",
        "Publish via npm Trusted Publisher OIDC",
    ):
        assert claim not in DOC, f"runbook still claims tokenless npm publishing: {claim!r}"


def test_pypi_auth_is_documented_as_the_workflow_implements_it() -> None:
    """PyPI really is tokenless — that half of the doc must stay."""
    assert "pypa/gh-action-pypi-publish" in PY_WORKFLOW
    assert "PYPI_API_TOKEN" not in PY_WORKFLOW
    assert "PyPI Trusted Publishing" in DOC


@pytest.mark.parametrize(
    "workflow_marker,doc_claim",
    [
        ("npm install -g npm@", "the workflow does this automatically"),
        ("npm-version-upgrade", "the workflow does this automatically"),
    ],
)
def test_no_troubleshooting_row_for_a_step_that_does_not_exist(workflow_marker: str, doc_claim: str) -> None:
    if workflow_marker not in TS_WORKFLOW:
        assert doc_claim not in DOC, (
            f"the runbook tells operators the workflow does something ({doc_claim!r}) "
            f"that no step in release-sdk-typescript.yml performs"
        )


def test_the_spec_drift_check_actually_runs_in_ci() -> None:
    """Every SDK doc points at this gate as the thing that stops the published
    contract drifting from the routes. It is a step in the consolidated lint
    job, not a job of its own — assert the invocation, not the job name."""
    lint_sh = REPO_ROOT / "scripts" / "ci" / "lint.sh"
    if not lint_sh.exists():
        pytest.skip("scripts/ci/lint.sh not present (public checkout)")
    assert "gen_sdk_openapi.py --check" in lint_sh.read_text()
