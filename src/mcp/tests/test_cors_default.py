# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for CORS default configuration."""
from __future__ import annotations

from unittest.mock import patch


def test_cors_default_is_not_wildcard():
    """Default CORS origins should be localhost, not wildcard."""
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("CORS_ORIGINS", None)
        default = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173,http://localhost:8888")
        origins = [o.strip() for o in default.split(",") if o.strip()]
        assert "*" not in origins
        assert "http://localhost:3000" in origins


def test_cors_default_includes_all_dev_ports():
    """Default CORS origins should include all local dev service ports."""
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("CORS_ORIGINS", None)
        default = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173,http://localhost:8888")
        origins = [o.strip() for o in default.split(",") if o.strip()]
        assert "http://localhost:3000" in origins  # React GUI (Docker)
        assert "http://localhost:5173" in origins  # Vite dev server
        assert "http://localhost:8888" in origins  # MCP API


def test_cors_env_override():
    """CORS_ORIGINS env var should override the default."""
    with patch.dict("os.environ", {"CORS_ORIGINS": "http://192.168.1.42:3000"}, clear=False):
        import os
        val = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173,http://localhost:8888")
        origins = [o.strip() for o in val.split(",") if o.strip()]
        assert origins == ["http://192.168.1.42:3000"]


def test_cors_wildcard_override():
    """Setting CORS_ORIGINS=* should allow wildcard (opt-in LAN access)."""
    with patch.dict("os.environ", {"CORS_ORIGINS": "*"}, clear=False):
        import os
        val = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173,http://localhost:8888")
        origins = [o.strip() for o in val.split(",") if o.strip()]
        assert origins == ["*"]
