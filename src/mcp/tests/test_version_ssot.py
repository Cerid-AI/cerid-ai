"""Task 16: single source of truth for the package version."""
from __future__ import annotations


def test_get_version_reads_pyproject():
    from core.utils.version import get_version
    v = get_version()
    # Strong shape check
    import re
    assert re.match(r"^\d+\.\d+\.\d+", v), f"not semver: {v}"


def test_version_consistency_root_and_health():
    """/ and /health must return the same version string."""
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    root = c.get("/").json()
    health = c.get("/health").json()
    assert root["version"] == health["version"], (
        f"version drift: / has {root['version']}, /health has {health['version']}"
    )
    # And it matches the SSOT
    from core.utils.version import get_version
    assert root["version"] == get_version()


def test_openapi_version_matches():
    from fastapi.testclient import TestClient

    from app.main import app
    from core.utils.version import get_version

    c = TestClient(app)
    openapi = c.get("/openapi.json").json()
    assert openapi["info"]["version"] == get_version()


def test_api_v1_routes_absent():
    """The /api/v1/* dual mount is retired — routes live only at root."""
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    openapi = c.get("/openapi.json").json()
    paths = list(openapi.get("paths", {}).keys())
    # SDK endpoints intentionally live at /sdk/v1/* — those are allowed.
    # No /api/v1/ duplicates should remain.
    api_v1_paths = [p for p in paths if p.startswith("/api/v1/")]
    assert api_v1_paths == [], f"legacy /api/v1 routes still present: {api_v1_paths[:5]}"
