# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""F033/F175 — docs/PROVIDERS.md must not document a provider the API rejects.

The doc offered ``ollama`` for EMBEDDINGS_PROVIDER / RERANK_PROVIDER. PATCH
/settings 400s that value and no dispatch branch exists for it, so an operator
who set it in .env silently got the in-process CPU path while the doc told them
the GPU daemon was serving. This gate reads the values out of the doc and puts
each one through the endpoint that validates them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_DOC = Path(__file__).resolve().parents[3] / "docs" / "PROVIDERS.md"


def _documented_values(var: str) -> set[str]:
    """Values PROVIDERS.md offers for ``var`` (the assignment + its `# or:` list)."""
    pattern = re.compile(rf"^{re.escape(var)}=(\S+)\s*(?:#\s*or:\s*(.+?))?$", re.M)
    match = pattern.search(_DOC.read_text(encoding="utf-8"))
    assert match, f"{var} is no longer documented in {_DOC.name}"
    values = {match.group(1)}
    if match.group(2):
        values |= {v.strip() for v in match.group(2).split("|") if v.strip()}
    return values


@pytest.fixture
def settings_client(monkeypatch):
    from app.routers.settings import router

    # PATCH /settings writes the env plane — keep the mutation inside the test.
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "sidecar")
    monkeypatch.setenv("RERANK_PROVIDER", "sidecar")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize(
    ("doc_var", "field"),
    [
        ("EMBEDDINGS_PROVIDER", "embeddings_provider"),
        ("RERANK_PROVIDER", "rerank_provider"),
    ],
)
def test_documented_providers_are_accepted(settings_client, doc_var, field):
    rejected = {
        value: settings_client.patch("/settings", json={field: value}).status_code
        for value in sorted(_documented_values(doc_var))
    }
    rejected = {v: code for v, code in rejected.items() if code != 200}

    assert not rejected, (
        f"PROVIDERS.md documents {doc_var} values the API rejects: {rejected}"
    )
