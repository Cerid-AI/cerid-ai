# Cerid AI — Feature Tier Matrix

> **Canonical tier-gating location:** `config/features.py`
>
> **Decorator:** `@require_feature("feature_name")` for async endpoints
> **Sync helper:** `check_feature("feature_name")` / `check_tier("pro")`
> **Read-only check:** `is_feature_enabled("feature_name")` / `is_tier_met("pro")`

## Feature Matrix

| Feature | Community | Pro | Enterprise | Gate mechanism |
|---------|-----------|-----|------------|----------------|
| Core RAG (query, ingest, search) | Yes | Yes | Yes | Always enabled |
| Hallucination detection | Yes | Yes | Yes | `ENABLE_HALLUCINATION_CHECK` toggle |
| Memory layer | Yes | Yes | Yes | `ENABLE_MEMORY_EXTRACTION` toggle |
| Web search fallback | Yes | Yes | Yes | Always enabled |
| SDK API (`/sdk/v1/`) | Yes | Yes | Yes | Always enabled |
| Ollama local LLM | Yes | Yes | Yes | `OLLAMA_ENABLED` toggle |
| Self-RAG validation | Yes | Yes | Yes | `ENABLE_SELF_RAG` toggle |
| Semantic cache | Yes | Yes | Yes | `ENABLE_SEMANTIC_CACHE` toggle |
| Adaptive retrieval | Yes | Yes | Yes | `ENABLE_ADAPTIVE_RETRIEVAL` toggle |
| Query decomposition | Yes | Yes | Yes | `ENABLE_QUERY_DECOMPOSITION` toggle |
| Smart routing | Yes | Yes | Yes | `ENABLE_MODEL_ROUTER` toggle |
| Streaming verification | Yes | Yes | Yes | Always enabled |
| Metamorphic verification | -- | Yes | Yes | `FEATURE_FLAGS["semantic_dedup"]` |
| Graph RAG (LightRAG) | -- | Yes | Yes | Pro-tier plugin |
| Multi-modal OCR | -- | Yes | Yes | `FEATURE_FLAGS["ocr_parsing"]` |
| Multi-modal audio | -- | Yes | Yes | `FEATURE_FLAGS["audio_transcription"]` |
| Multi-modal vision | -- | Yes | Yes | `FEATURE_FLAGS["image_understanding"]` |
| Visual workflow builder | -- | Yes | Yes | Pro-tier plugin (`workflow/`) |
| Advanced analytics plugin | -- | Yes | Yes | `FEATURE_FLAGS["advanced_analytics"]` |
| Multi-user auth (JWT) | -- | -- | Yes | `CERID_MULTI_USER=true` + `CERID_JWT_SECRET` |
| SLA and priority support | -- | -- | Yes | Contractual |

## Tier Configuration

Set the tier via environment variable:

```bash
CERID_TIER=community   # default — open-source features only
CERID_TIER=pro         # enables commercial plugins and pro features
```

## Adding New Tier-Gated Features

1. Add the feature flag to `FEATURE_FLAGS` in `config/features.py`
2. Use `@require_feature("flag_name")` on async router endpoints
3. Use `check_feature("flag_name")` in sync service functions
4. Use `check_tier("pro")` for blanket tier checks (e.g. plugin loading)
5. **Never** use inline `if config.FEATURE_TIER == "pro"` checks
6. Update this matrix

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
