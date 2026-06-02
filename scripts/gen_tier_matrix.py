#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Generate docs/TIER_MATRIX.md from the feature-flag source of truth.

The tier of every flag comes from ``config.features`` (``_get_feature_tier``
+ ``FEATURE_BUCKETS`` + ``FEATURE_FLAGS``) — the same logic the runtime gates
use — so the published matrix can never drift from the code. Friendly display
labels live in ``LABELS`` below (the one thing not derivable from the flag
name); a flag without a label falls back to a prettified name.

Usage:
    python scripts/gen_tier_matrix.py            # regenerate docs/TIER_MATRIX.md
    python scripts/gen_tier_matrix.py --check     # drift guard (exit 1 on mismatch)
    python scripts/gen_tier_matrix.py --stdout    # print to stdout

Unlike gen_env_example.py / gen_router_registry.py (AST-only, slim-container),
this imports config.features to reuse the real tier-resolution logic rather
than re-implementing it (which would be a second source of truth). It is
enforced in CI via test_tier_matrix_drift.py, which runs in the deps-complete
``test`` job — mirroring lint-pro-gating.py + test_pro_gating_contract.py.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_ROOT = REPO_ROOT / "src" / "mcp"
OUTPUT_FILE = REPO_ROOT / "docs" / "TIER_MATRIX.md"

# Friendly display labels. A flag missing here falls back to a prettified
# name; the drift gate still forces a doc regen when a flag is added, which
# surfaces the missing label in review.
LABELS = {
    "advanced_analytics": "Advanced analytics",
    "apple_calendar_eventkit": "Apple Calendar (EventKit)",
    "apple_mail_reader": "Apple Mail reader",
    "apple_notes_reader": "Apple Notes reader",
    "apple_photos_reader": "Apple Photos reader",
    "apple_silicon_ml": "Apple Silicon ML acceleration",
    "audio_transcription": "Audio transcription",
    "audio_transcription_plain": "Audio transcription (plain)",
    "audit_logging": "Audit logging",
    "basic_workflows": "Workflows (basic)",
    "calendar_stitching": "Meeting calendar stitching",
    "calendar_sync": "Calendar sync",
    "custom_smart_rag": "Custom Smart RAG",
    "daily_digest": "Daily digest",
    "docling_parser": "Docling document parser",
    "encryption_at_rest": "Encryption at rest",
    "file_upload_gui": "File upload (GUI)",
    "gmail_connector": "Gmail connector",
    "google_calendar_sync": "Google Calendar sync",
    "hierarchical_taxonomy": "Hierarchical taxonomy",
    "image_understanding": "Image understanding",
    "imessage_reader": "iMessage reader",
    "inbox_triage": "AI inbox triage",
    "keychain_secrets": "Keychain secrets",
    "live_metrics": "Live metrics",
    "meeting_diarization": "Meeting diarization",
    "meeting_summary": "Meeting summary",
    "menu_bar_mode": "Menu-bar mode",
    "metamorphic_verification": "Metamorphic verification",
    "multi_user": "Multi-user",
    "ocr_parsing": "OCR (scanned PDFs)",
    "outlook_calendar_sync": "Outlook Calendar sync",
    "outlook_connector": "Outlook connector",
    "parent_child_retrieval": "Parent-child retrieval",
    "priority_support": "Priority support",
    "private_mode": "Private Mode",
    "quicklook_preview": "QuickLook preview",
    "reminders_eventkit": "Apple Reminders (EventKit)",
    "safari_reading_list": "Safari Reading List",
    "semantic_dedup": "Semantic deduplication",
    "share_sheet": "Share Sheet",
    "shortcuts_actions": "Shortcuts actions",
    "sparkle_updates": "Sparkle auto-updates",
    "spotlight_donation": "Spotlight donation",
    "spotlight_integration": "Spotlight integration",
    "sso_saml": "SSO / SAML",
    "tcc_wizard": "TCC permissions wizard",
    "truth_audit": "Truth audit",
    "universal_binary": "Universal binary",
    "voice_memos_watch": "Voice Memos watcher",
}

# Section ordering. Bucket sections come first (in this order), then the
# tier-derived catch-alls for flags that belong to no bucket.
_BUCKET_SECTIONS = [
    ("pro_intelligence", "Pro Intelligence"),
    ("pro_meeting_capture", "Meeting Capture — Pro"),
    ("pro_cloud_connectors", "Cloud Connectors — Pro"),
    ("pro_apple_connectors", "Apple Connectors — Pro"),
    ("mac_native", "macOS Native — Community"),
]
_TICK = {  # tier -> (Core, Pro, Enterprise)
    "community": ("✓", "✓", "✓"),
    "pro": ("—", "✓", "✓"),
    "enterprise": ("—", "—", "✓"),
}


def _label(flag: str) -> str:
    return LABELS.get(flag, flag.replace("_", " ").capitalize())


def _collect() -> list[tuple[str, list[tuple[str, str]]]]:
    """Return ordered [(section_title, [(flag, tier), ...]), ...]."""
    sys.path.insert(0, str(MCP_ROOT))
    from config.features import (  # noqa: PLC0415
        FEATURE_BUCKETS,
        FEATURE_FLAGS,
        _get_feature_tier,
    )

    flag_bucket = {fl: b for b, flags in FEATURE_BUCKETS.items() for fl in flags}
    tier = {fl: _get_feature_tier(fl) for fl in FEATURE_FLAGS}

    sections: list[tuple[str, list[tuple[str, str]]]] = []
    for bucket, title in _BUCKET_SECTIONS:
        members = sorted(fl for fl in FEATURE_FLAGS if flag_bucket.get(fl) == bucket)
        if members:
            sections.append((title, [(fl, tier[fl]) for fl in members]))

    bucketed = set(flag_bucket)
    for want_tier, title in (
        ("pro", "Other Pro Features"),
        ("enterprise", "Enterprise"),
        ("community", "Other Community Features"),
    ):
        members = sorted(
            fl
            for fl in FEATURE_FLAGS
            if fl not in bucketed and tier[fl] == want_tier
        )
        if members:
            sections.append((title, [(fl, tier[fl]) for fl in members]))

    return sections


def _render(sections: list[tuple[str, list[tuple[str, str]]]]) -> str:
    total = sum(len(rows) for _, rows in sections)
    out: list[str] = []
    out.append("# Cerid AI — Feature Tier Matrix")
    out.append("")
    out.append(
        "> GENERATED FILE — do not edit by hand. Regenerate with "
        "`python scripts/gen_tier_matrix.py`."
    )
    out.append(
        "> Source of truth: `config/features.py` "
        "(`_get_feature_tier` + `FEATURE_BUCKETS`); enforced by "
        "`tests/test_tier_matrix_drift.py`."
    )
    out.append(f"> {total} feature flags across {len(sections)} sections.")
    out.append("")
    out.append("## Tiers")
    out.append("")
    out.append("| Tier | License | Audience | Price |")
    out.append("|------|---------|----------|-------|")
    out.append(
        "| **Cerid Core** | Apache-2.0 | Developers, researchers, "
        "personal use | Free |"
    )
    out.append(
        "| **Cerid Pro** | BSL-1.1 | Business and power users | "
        "$15/mo · $144/yr |"
    )
    out.append(
        "| **Cerid Enterprise** | Commercial | Regulated and large "
        "organizations | Contact |"
    )
    out.append("")
    out.append("## Feature Matrix")
    out.append("")
    for title, rows in sections:
        out.append(f"### {title}")
        out.append("")
        out.append("| Feature | Core | Pro | Enterprise | Gate |")
        out.append("|---------|------|-----|------------|------|")
        for flag, tier in rows:
            core, pro, ent = _TICK[tier]
            out.append(f"| {_label(flag)} | {core} | {pro} | {ent} | `{flag}` |")
        out.append("")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="Drift mode — exit 1 on mismatch")
    ap.add_argument("--stdout", action="store_true", help="Emit to stdout")
    args = ap.parse_args()

    rendered = _render(_collect())

    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    if args.check:
        if not OUTPUT_FILE.exists():
            print(
                f"::error::{OUTPUT_FILE.relative_to(REPO_ROOT)} missing — "
                "run: python scripts/gen_tier_matrix.py",
                file=sys.stderr,
            )
            return 1
        current = OUTPUT_FILE.read_text(encoding="utf-8")
        if current != rendered:
            diff = "\n".join(
                difflib.unified_diff(
                    current.splitlines(),
                    rendered.splitlines(),
                    fromfile=str(OUTPUT_FILE.relative_to(REPO_ROOT)),
                    tofile="expected",
                    lineterm="",
                )
            )
            print(
                f"::error::{OUTPUT_FILE.relative_to(REPO_ROOT)} is out of date — "
                "regenerate with: python scripts/gen_tier_matrix.py\n" + diff,
                file=sys.stderr,
            )
            return 1
        return 0

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT_FILE.relative_to(REPO_ROOT)} ({sum(len(r) for _, r in _collect())} flags)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
