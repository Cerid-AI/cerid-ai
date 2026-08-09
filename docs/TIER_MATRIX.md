# Cerid AI — Feature Tier Matrix

> GENERATED FILE — do not edit by hand. Regenerate with `python scripts/gen_tier_matrix.py`.
> Source of truth: `config/features.py` (`_get_feature_tier` + `FEATURE_BUCKETS`); enforced by `tests/test_tier_matrix_drift.py`.
> 53 feature flags across 9 sections.

## Tiers

| Tier | License | Audience | Price |
|------|---------|----------|-------|
| **Cerid Core** | FSL-1.1-ALv2 (source-available) | Developers, researchers, personal use | Free |
| **Cerid Pro** | BUSL-1.1 | Business and power users | $15/mo · $144/yr |
| **Cerid Enterprise** | Commercial | Regulated and large organizations | Contact |

## Feature Matrix

### Pro Intelligence

| Feature | Core | Pro | Enterprise | Gate |
|---------|------|-----|------------|------|
| Advanced analytics | — | ✓ | ✓ | `advanced_analytics` |
| Custom Smart RAG | — | ✓ | ✓ | `custom_smart_rag` |
| Daily digest | — | ✓ | ✓ | `daily_digest` |
| AI inbox triage | — | ✓ | ✓ | `inbox_triage` |
| Metamorphic verification | — | ✓ | ✓ | `metamorphic_verification` |

### Pro Visualization

| Feature | Core | Pro | Enterprise | Gate |
|---------|------|-----|------------|------|
| Visualization analytics overlay | — | ✓ | ✓ | `pro_visualization_analytics` |
| Visualization timeline overlay | — | ✓ | ✓ | `pro_visualization_timeline` |
| Guided graph tour | — | ✓ | ✓ | `pro_visualization_tour` |

### Meeting Capture — Pro

| Feature | Core | Pro | Enterprise | Gate |
|---------|------|-----|------------|------|
| Meeting calendar stitching | — | ✓ | ✓ | `calendar_stitching` |
| Meeting diarization | — | ✓ | ✓ | `meeting_diarization` |
| Meeting summary | — | ✓ | ✓ | `meeting_summary` |

### Cloud Connectors — Pro

| Feature | Core | Pro | Enterprise | Gate |
|---------|------|-----|------------|------|
| Gmail connector | — | ✓ | ✓ | `gmail_connector` |
| Google Calendar sync | — | ✓ | ✓ | `google_calendar_sync` |
| Outlook Calendar sync | — | ✓ | ✓ | `outlook_calendar_sync` |
| Outlook connector | — | ✓ | ✓ | `outlook_connector` |

### Apple Connectors — Pro

| Feature | Core | Pro | Enterprise | Gate |
|---------|------|-----|------------|------|
| Apple Calendar (EventKit) | — | ✓ | ✓ | `apple_calendar_eventkit` |
| Apple Mail reader | — | ✓ | ✓ | `apple_mail_reader` |
| Apple Notes reader | — | ✓ | ✓ | `apple_notes_reader` |
| Apple Photos reader | — | ✓ | ✓ | `apple_photos_reader` |
| iMessage reader | — | ✓ | ✓ | `imessage_reader` |
| Apple Reminders (EventKit) | — | ✓ | ✓ | `reminders_eventkit` |

### macOS Native — Community

| Feature | Core | Pro | Enterprise | Gate |
|---------|------|-----|------------|------|
| Apple Silicon ML acceleration | ✓ | ✓ | ✓ | `apple_silicon_ml` |
| Keychain secrets | ✓ | ✓ | ✓ | `keychain_secrets` |
| Menu-bar mode | ✓ | ✓ | ✓ | `menu_bar_mode` |
| QuickLook preview | Coming in 1.0.x | Coming in 1.0.x | Coming in 1.0.x | `quicklook_preview` |
| Safari Reading List | ✓ | ✓ | ✓ | `safari_reading_list` |
| Share Sheet | Coming in 1.0.x | Coming in 1.0.x | Coming in 1.0.x | `share_sheet` |
| Shortcuts actions | Coming in 1.0.x | Coming in 1.0.x | Coming in 1.0.x | `shortcuts_actions` |
| Sparkle auto-updates | ✓ | ✓ | ✓ | `sparkle_updates` |
| Spotlight integration | ✓ | ✓ | ✓ | `spotlight_integration` |
| TCC permissions wizard | ✓ | ✓ | ✓ | `tcc_wizard` |
| Universal binary | ✓ | ✓ | ✓ | `universal_binary` |
| Voice Memos watcher | ✓ | ✓ | ✓ | `voice_memos_watch` |

### Other Pro Features

| Feature | Core | Pro | Enterprise | Gate |
|---------|------|-----|------------|------|
| Calendar sync | — | ✓ | ✓ | `calendar_sync` |
| Spotlight donation | — | ✓ | ✓ | `spotlight_donation` |

### Enterprise

| Feature | Core | Pro | Enterprise | Gate |
|---------|------|-----|------------|------|
| Audit logging | — | — | ✓ | `audit_logging` |
| Multi-user | — | — | ✓ | `multi_user` |
| Priority support | — | — | ✓ | `priority_support` |
| SSO / SAML | — | — | ✓ | `sso_saml` |

### Other Community Features

| Feature | Core | Pro | Enterprise | Gate |
|---------|------|-----|------------|------|
| Audio transcription | ✓ | ✓ | ✓ | `audio_transcription` |
| Audio transcription (plain) | ✓ | ✓ | ✓ | `audio_transcription_plain` |
| Workflows (basic) | ✓ | ✓ | ✓ | `basic_workflows` |
| Docling document parser | ✓ | ✓ | ✓ | `docling_parser` |
| Encryption at rest | ✓ | ✓ | ✓ | `encryption_at_rest` |
| File upload (GUI) | ✓ | ✓ | ✓ | `file_upload_gui` |
| Hierarchical taxonomy | ✓ | ✓ | ✓ | `hierarchical_taxonomy` |
| Image understanding | ✓ | ✓ | ✓ | `image_understanding` |
| Live metrics | ✓ | ✓ | ✓ | `live_metrics` |
| OCR (scanned PDFs) | ✓ | ✓ | ✓ | `ocr_parsing` |
| Parent-child retrieval | ✓ | ✓ | ✓ | `parent_child_retrieval` |
| Private Mode | ✓ | ✓ | ✓ | `private_mode` |
| Semantic deduplication | ✓ | ✓ | ✓ | `semantic_dedup` |
| Truth audit | ✓ | ✓ | ✓ | `truth_audit` |

