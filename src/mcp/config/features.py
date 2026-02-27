# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Feature flags, toggles, and plugin configuration."""
from __future__ import annotations

import logging as _logging
import os

# ---------------------------------------------------------------------------
# Plugin System & Feature Tiers
# ---------------------------------------------------------------------------
# Feature tier: "community" (OSS) or "pro" (commercial plugins enabled)
FEATURE_TIER = os.getenv("CERID_TIER", "community")

# Plugin directory (relative to app root or absolute path)
PLUGIN_DIR = os.getenv("CERID_PLUGIN_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins"))

# Comma-separated list of plugin names to load (empty = auto-discover all)
_enabled_plugins_raw = os.getenv("CERID_ENABLED_PLUGINS", "")
ENABLED_PLUGINS = [p.strip() for p in _enabled_plugins_raw.split(",") if p.strip()] if _enabled_plugins_raw else []

# Feature flags: controls what's available per tier
# Community features are always enabled; pro features require CERID_TIER=pro
FEATURE_FLAGS = {
    # Pro-only features (disabled in community tier)
    "ocr_parsing":         FEATURE_TIER == "pro",
    "audio_transcription": FEATURE_TIER == "pro",
    "image_understanding": FEATURE_TIER == "pro",
    "semantic_dedup":      FEATURE_TIER == "pro",
    "advanced_analytics":  FEATURE_TIER == "pro",
    "multi_user":          FEATURE_TIER == "pro",
    # Community features (always enabled)
    "hierarchical_taxonomy": True,
    "file_upload_gui":       True,
    "encryption_at_rest":    True,
    "truth_audit":           True,
    "live_metrics":          True,
}

# ---------------------------------------------------------------------------
# Feature toggles
# ---------------------------------------------------------------------------
ENABLE_HALLUCINATION_CHECK = os.getenv("ENABLE_HALLUCINATION_CHECK", "false").lower() == "true"
ENABLE_FEEDBACK_LOOP = os.getenv("ENABLE_FEEDBACK_LOOP", "false").lower() == "true"
ENABLE_MEMORY_EXTRACTION = os.getenv("ENABLE_MEMORY_EXTRACTION", "false").lower() == "true"
ENABLE_ENCRYPTION = os.getenv("ENABLE_ENCRYPTION", "false").lower() == "true"
# CERID_ENCRYPTION_KEY is read directly from env by utils/encryption.py
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# ---------------------------------------------------------------------------
# Smart Orchestration
# NOTE: ENABLE_MODEL_ROUTER and MONTHLY_BUDGET are client-side hints only.
# They are exposed to the GUI via GET /settings but never enforced server-side.
# ---------------------------------------------------------------------------
ENABLE_MODEL_ROUTER = os.getenv("ENABLE_MODEL_ROUTER", "false").lower() == "true"
COST_SENSITIVITY = os.getenv("COST_SENSITIVITY", "medium")  # low/medium/high
MONTHLY_BUDGET = float(os.getenv("MONTHLY_BUDGET", "0"))  # USD, 0 = unlimited

# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------
_config_logger = _logging.getLogger("ai-companion.config")

COST_SENSITIVITY = COST_SENSITIVITY.strip().lower()
if COST_SENSITIVITY not in ("low", "medium", "high"):
    _config_logger.warning(
        "Invalid COST_SENSITIVITY=%r, defaulting to 'medium'", COST_SENSITIVITY
    )
    COST_SENSITIVITY = "medium"
