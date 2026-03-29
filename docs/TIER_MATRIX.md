# Cerid AI — Feature Tier Matrix

> Last updated: 2026-03-29
> Tier enforcement: `config/features.py` → `@require_feature()` decorator

## Tier Overview

| Tier | License | Target | Price |
|------|---------|--------|-------|
| **Cerid Core** | Apache-2.0 | Developers, researchers, personal use | Free |
| **Cerid Pro** | BSL-1.1 | Business, intelligence analysts, security teams | Paid |
| **Cerid Enterprise** | Commercial | IC, USG, large organizations | Contact |

## Feature Matrix

| Feature | Core | Pro | Enterprise | Gate |
|---------|------|-----|------------|------|
| **RAG & Retrieval** | | | | |
| Core RAG (query, ingest, search) | ✓ | ✓ | ✓ | — |
| Semantic cache | ✓ | ✓ | ✓ | — |
| Adaptive retrieval | ✓ | ✓ | ✓ | — |
| Query decomposition | ✓ | ✓ | ✓ | — |
| HyDE fallback | ✓ | ✓ | ✓ | — |
| Retrieval cache | ✓ | ✓ | ✓ | — |
| Smart auto-RAG | ✓ | ✓ | ✓ | — |
| Parent-child chunking | ✓ | ✓ | ✓ | env flag |
| Graph RAG (LightRAG) | — | ✓ | ✓ | `graph_rag` |
| **Verification** | | | | |
| Hallucination detection | ✓ | ✓ | ✓ | — |
| Streaming verification | ✓ | ✓ | ✓ | — |
| Metamorphic verification | — | ✓ | ✓ | `metamorphic_verification` |
| **Knowledge Management** | | | | |
| Memory layer | ✓ | ✓ | ✓ | — |
| Web search fallback | ✓ | ✓ | ✓ | — |
| External data sources | ✓ | ✓ | ✓ | — |
| **Multi-Modal** | | | | |
| OCR (scanned PDFs) | — | ✓ | ✓ | `ocr_parsing` |
| Audio transcription | — | ✓ | ✓ | `audio_transcription` |
| Vision (image analysis) | — | ✓ | ✓ | `image_understanding` |
| **Tools & Workflow** | | | | |
| Visual workflow builder | — | ✓ | ✓ | plugin tier |
| Advanced analytics | — | ✓ | ✓ | `advanced_analytics` |
| SDK API (`/sdk/v1/`) | ✓ | ✓ | ✓ | — |
| Ollama local LLM | ✓ | ✓ | ✓ | — |
| **Infrastructure** | | | | |
| Multi-user JWT auth | — | — | ✓ | `multi_user` |
| SSO/SAML | — | — | ✓ | `sso_saml` |
| Audit logging | — | — | ✓ | `audit_logging` |
| SLA & priority support | — | — | ✓ | contractual |

## Tier Configuration

Set the tier via environment variable:

```bash
CERID_TIER=community   # default — open-source features only
CERID_TIER=pro         # enables commercial plugins and pro features
CERID_TIER=enterprise  # enables all features including multi-user, SSO, audit logging
```

Enterprise includes all Pro features. Pro includes all Core features.

## Adding New Tier-Gated Features

1. Add the feature flag to `FEATURE_FLAGS` in `config/features.py`
2. Update `_get_feature_tier()` to return the correct minimum tier
3. Use `@require_feature("flag_name")` on async router endpoints
4. Use `check_feature("flag_name")` in sync service functions
5. Use `check_tier("pro")` or `check_tier("enterprise")` for blanket tier checks
6. **Never** use inline `if config.FEATURE_TIER == "pro"` checks
7. Update this matrix

## Plugin Tier Gating

Plugins declare their tier in `manifest.json`:

```json
{
  "name": "ocr",
  "tier": "pro"
}
```

The plugin loader (`plugins/__init__.py`) uses `is_tier_met()` to skip plugins
that require a higher tier. The plugin router uses `check_tier()` to block
enable requests for unmet tiers.
