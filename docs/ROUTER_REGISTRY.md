# Router Registry

> **Auto-generated** by `scripts/gen_router_registry.py`.
> Regenerate with: `python scripts/gen_router_registry.py`.
> CI drift gate: `python scripts/gen_router_registry.py --check`.

Every `@router.*` decorator shipped in the public (OSS Apache-2.0) distribution.
Internal-only routers (billing, trading SDK, ops endpoints) are stripped and
not documented here; see the internal repo for the full registry.

**Total routes:** 249

| Method | Path | Handler | Module | Tags | Build |
|--------|------|---------|--------|------|-------|
| GET | `/.well-known/agent.json` | `agent_card` | `src/mcp/app/routers/a2a.py` | a2a |  |
| POST | `/a2a/tasks` | `create_task` | `src/mcp/app/routers/a2a.py` | a2a |  |
| GET | `/a2a/tasks/{task_id}` | `get_task` | `src/mcp/app/routers/a2a.py` | a2a |  |
| POST | `/a2a/tasks/{task_id}/cancel` | `cancel_task` | `src/mcp/app/routers/a2a.py` | a2a |  |
| GET | `/a2a/tasks/{task_id}/history` | `get_task_history` | `src/mcp/app/routers/a2a.py` | a2a |  |
| DELETE | `/clear` | `clear` | `src/mcp/app/routers/agent_console.py` | agent-console,agent-console |  |
| DELETE | `/clear` | `activity_clear` | `src/mcp/app/routers/agent_console.py` | agent-console,agent-console |  |
| GET | `/recent` | `recent_events` | `src/mcp/app/routers/agent_console.py` | agent-console,agent-console |  |
| GET | `/recent` | `activity_recent` | `src/mcp/app/routers/agent_console.py` | agent-console,agent-console |  |
| GET | `/stream` | `stream_events` | `src/mcp/app/routers/agent_console.py` | agent-console,agent-console |  |
| GET | `/stream` | `activity_stream` | `src/mcp/app/routers/agent_console.py` | agent-console,agent-console |  |
| POST | `/agent/audit` | `audit_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/agent/curate` | `curate_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/agent/curate/estimate` | `curate_estimate_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/agent/hallucination` | `hallucination_check_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/agent/hallucination/feedback` | `claim_feedback_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| GET | `/agent/hallucination/{conversation_id}` | `hallucination_report_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/agent/maintain` | `maintain_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/agent/memory/archive` | `memory_archive_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/agent/memory/extract` | `memory_extract_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/agent/memory/recall` | `memory_recall_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/agent/query` | `agent_query_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/agent/rectify` | `rectify_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/agent/triage` | `triage_file_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/agent/triage/batch` | `triage_batch_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/agent/verify-stream` | `verify_stream_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/chat/compress` | `compress_history_endpoint` | `src/mcp/app/routers/agents.py` |  |  |
| POST | `/verification/save` | `save_verification_report` | `src/mcp/app/routers/agents.py` |  |  |
| GET | `/verification/{conversation_id}` | `get_verification_report` | `src/mcp/app/routers/agents.py` |  |  |
| GET | `/artifacts` | `list_artifacts_endpoint` | `src/mcp/app/routers/artifacts.py` |  |  |
| GET | `/artifacts/{artifact_id}` | `artifact_detail_endpoint` | `src/mcp/app/routers/artifacts.py` |  |  |
| POST | `/artifacts/{artifact_id}/feedback` | `artifact_feedback_endpoint` | `src/mcp/app/routers/artifacts.py` |  |  |
| GET | `/artifacts/{artifact_id}/related` | `related_artifacts_endpoint` | `src/mcp/app/routers/artifacts.py` |  |  |
| POST | `/recategorize` | `recategorize_endpoint` | `src/mcp/app/routers/artifacts.py` |  |  |
| POST | `/login` | `login` | `src/mcp/app/routers/auth.py` | auth |  |
| POST | `/logout` | `logout` | `src/mcp/app/routers/auth.py` | auth |  |
| GET | `/me` | `me` | `src/mcp/app/routers/auth.py` | auth |  |
| DELETE | `/me/api-key` | `delete_api_key` | `src/mcp/app/routers/auth.py` | auth |  |
| PUT | `/me/api-key` | `set_api_key` | `src/mcp/app/routers/auth.py` | auth |  |
| GET | `/me/api-key/status` | `api_key_status` | `src/mcp/app/routers/auth.py` | auth |  |
| GET | `/me/usage` | `user_usage` | `src/mcp/app/routers/auth.py` | auth |  |
| POST | `/refresh` | `refresh` | `src/mcp/app/routers/auth.py` | auth |  |
| POST | `/register` | `register` | `src/mcp/app/routers/auth.py` | auth |  |
| GET | `` | `list_automations` | `src/mcp/app/routers/automations.py` | automations |  |
| POST | `` | `create_automation` | `src/mcp/app/routers/automations.py` | automations |  |
| GET | `/presets` | `get_presets` | `src/mcp/app/routers/automations.py` | automations |  |
| DELETE | `/{automation_id}` | `delete_automation` | `src/mcp/app/routers/automations.py` | automations |  |
| GET | `/{automation_id}` | `get_automation` | `src/mcp/app/routers/automations.py` | automations |  |
| PUT | `/{automation_id}` | `update_automation` | `src/mcp/app/routers/automations.py` | automations |  |
| POST | `/{automation_id}/disable` | `disable_automation` | `src/mcp/app/routers/automations.py` | automations |  |
| POST | `/{automation_id}/enable` | `enable_automation` | `src/mcp/app/routers/automations.py` | automations |  |
| GET | `/{automation_id}/history` | `get_history` | `src/mcp/app/routers/automations.py` | automations |  |
| POST | `/{automation_id}/run` | `trigger_manual_run` | `src/mcp/app/routers/automations.py` | automations |  |
| POST | `/chat/compress` | `compress_context` | `src/mcp/app/routers/chat.py` | chat |  |
| POST | `/chat/stream` | `chat_stream` | `src/mcp/app/routers/chat.py` | chat |  |
| GET | `/custom-agents` | `list_agents` | `src/mcp/app/routers/custom_agents.py` | custom-agents |  |
| POST | `/custom-agents` | `create_agent` | `src/mcp/app/routers/custom_agents.py` | custom-agents |  |
| POST | `/custom-agents/from-template/{template_id}` | `create_from_template` | `src/mcp/app/routers/custom_agents.py` | custom-agents |  |
| GET | `/custom-agents/templates` | `list_templates` | `src/mcp/app/routers/custom_agents.py` | custom-agents |  |
| DELETE | `/custom-agents/{agent_id}` | `delete_agent` | `src/mcp/app/routers/custom_agents.py` | custom-agents |  |
| GET | `/custom-agents/{agent_id}` | `get_agent` | `src/mcp/app/routers/custom_agents.py` | custom-agents |  |
| PATCH | `/custom-agents/{agent_id}` | `update_agent` | `src/mcp/app/routers/custom_agents.py` | custom-agents |  |
| POST | `/custom-agents/{agent_id}/query` | `query_agent` | `src/mcp/app/routers/custom_agents.py` | custom-agents |  |
| GET | `/data-sources` | `list_data_sources` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| GET | `/data-sources/bookmarks/detect` | `detect_bookmark_browsers` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| POST | `/data-sources/bookmarks/import` | `import_browser_bookmarks` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| GET | `/data-sources/bookmarks/status` | `bookmark_import_status` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| DELETE | `/data-sources/email` | `delete_email_source` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| POST | `/data-sources/email/configure` | `configure_email` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| POST | `/data-sources/email/import-emlx` | `import_emlx_file` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| POST | `/data-sources/email/poll-now` | `poll_email_now` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| GET | `/data-sources/email/status` | `email_status` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| POST | `/data-sources/query` | `query_data_sources` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| GET | `/data-sources/rss` | `list_rss_feeds` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| POST | `/data-sources/rss` | `add_rss_feed` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| POST | `/data-sources/rss/poll-all` | `poll_all_rss_feeds` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| DELETE | `/data-sources/rss/{feed_id}` | `delete_rss_feed` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| GET | `/data-sources/rss/{feed_id}/entries` | `list_rss_feed_entries` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| POST | `/data-sources/rss/{feed_id}/fetch-now` | `fetch_rss_feed_now` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| POST | `/data-sources/{name}/disable` | `disable_source` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| POST | `/data-sources/{name}/enable` | `enable_source` | `src/mcp/app/routers/data_sources.py` | data-sources |  |
| GET | `/digest` | `digest_endpoint` | `src/mcp/app/routers/digest.py` |  |  |
| GET | `` | `get_dlq_entries` | `src/mcp/app/routers/dlq.py` | admin-dlq |  |
| POST | `/retry/{entry_id}` | `retry_dlq_entry` | `src/mcp/app/routers/dlq.py` | admin-dlq |  |
| DELETE | `/{entry_id}` | `discard_dlq_entry` | `src/mcp/app/routers/dlq.py` | admin-dlq |  |
| GET | `/collections` | `list_collections_endpoint` | `src/mcp/app/routers/health.py` |  |  |
| GET | `/health` | `health_check_endpoint` | `src/mcp/app/routers/health.py` |  |  |
| GET | `/health/live` | `liveness_probe` | `src/mcp/app/routers/health.py` |  |  |
| GET | `/health/ping` | `health_ping` | `src/mcp/app/routers/health.py` |  |  |
| GET | `/health/status` | `health_status_endpoint` | `src/mcp/app/routers/health.py` |  |  |
| GET | `/plugins` | `plugins_endpoint` | `src/mcp/app/routers/health.py` |  |  |
| GET | `/scheduler` | `scheduler_status_endpoint` | `src/mcp/app/routers/health.py` |  |  |
| POST | `/ingest` | `ingest_endpoint` | `src/mcp/app/routers/ingestion.py` |  |  |
| POST | `/ingest/feedback` | `ingest_feedback_endpoint` | `src/mcp/app/routers/ingestion.py` |  |  |
| POST | `/ingest_batch` | `ingest_batch_endpoint` | `src/mcp/app/routers/ingestion.py` |  |  |
| POST | `/ingest_file` | `ingest_file_endpoint` | `src/mcp/app/routers/ingestion.py` |  |  |
| GET | `/ingest_log` | `ingest_log_endpoint` | `src/mcp/app/routers/ingestion.py` |  |  |
| GET | `/ingestion/progress` | `ingestion_progress_endpoint` | `src/mcp/app/routers/ingestion.py` |  |  |
| DELETE | `/admin/artifacts/{artifact_id}` | `delete_single_artifact` | `src/mcp/app/routers/kb_admin.py` | kb-admin |  |
| POST | `/admin/artifacts/{artifact_id}/reingest` | `reingest_artifact` | `src/mcp/app/routers/kb_admin.py` | kb-admin |  |
| POST | `/admin/collections/repair` | `repair_collection` | `src/mcp/app/routers/kb_admin.py` | kb-admin |  |
| GET | `/admin/kb/capabilities` | `get_parser_capabilities` | `src/mcp/app/routers/kb_admin.py` | kb-admin |  |
| POST | `/admin/kb/clear-domain/{domain}` | `clear_domain` | `src/mcp/app/routers/kb_admin.py` | kb-admin |  |
| GET | `/admin/kb/duplicates` | `list_duplicates` | `src/mcp/app/routers/kb_admin.py` | kb-admin |  |
| POST | `/admin/kb/duplicates/dismiss` | `dismiss_duplicates` | `src/mcp/app/routers/kb_admin.py` | kb-admin |  |
| POST | `/admin/kb/duplicates/merge` | `merge_duplicates` | `src/mcp/app/routers/kb_admin.py` | kb-admin |  |
| POST | `/admin/kb/rebuild-index` | `rebuild_indexes` | `src/mcp/app/routers/kb_admin.py` | kb-admin |  |
| POST | `/admin/kb/regenerate-summaries` | `regenerate_summaries` | `src/mcp/app/routers/kb_admin.py` | kb-admin |  |
| POST | `/admin/kb/rescore` | `rescore_artifacts` | `src/mcp/app/routers/kb_admin.py` | kb-admin |  |
| GET | `/admin/kb/stats` | `kb_stats` | `src/mcp/app/routers/kb_admin.py` | kb-admin |  |
| GET | `` | `list_mcp_servers` | `src/mcp/app/routers/mcp_client.py` | MCP Client |  |
| POST | `` | `add_mcp_server` | `src/mcp/app/routers/mcp_client.py` | MCP Client |  |
| DELETE | `/{name}` | `remove_mcp_server` | `src/mcp/app/routers/mcp_client.py` | MCP Client |  |
| POST | `/{name}/reconnect` | `reconnect_mcp_server` | `src/mcp/app/routers/mcp_client.py` | MCP Client |  |
| GET | `/{name}/tools` | `list_server_tools` | `src/mcp/app/routers/mcp_client.py` | MCP Client |  |
| POST | `/mcp/messages` | `mcp_messages` | `src/mcp/app/routers/mcp_sse.py` |  |  |
| GET | `/mcp/sse` | `mcp_sse_endpoint` | `src/mcp/app/routers/mcp_sse.py` |  |  |
| HEAD | `/mcp/sse` | `mcp_sse_head` | `src/mcp/app/routers/mcp_sse.py` |  |  |
| POST | `/mcp/sse` | `mcp_sse_post` | `src/mcp/app/routers/mcp_sse.py` |  |  |
| GET | `/memories` | `list_memories` | `src/mcp/app/routers/memories.py` |  |  |
| POST | `/memories/extract` | `extract_memories_endpoint` | `src/mcp/app/routers/memories.py` |  |  |
| DELETE | `/memories/{memory_id}` | `delete_memory` | `src/mcp/app/routers/memories.py` |  |  |
| PATCH | `/memories/{memory_id}` | `update_memory` | `src/mcp/app/routers/memories.py` |  |  |
| GET | `/assignments` | `get_assignments` | `src/mcp/app/routers/models.py` | models |  |
| PUT | `/assignments` | `update_assignments` | `src/mcp/app/routers/models.py` | models |  |
| GET | `/available` | `list_available_models` | `src/mcp/app/routers/models.py` | models |  |
| GET | `/updates` | `list_model_updates` | `src/mcp/app/routers/models.py` | models |  |
| POST | `/updates/check` | `check_model_updates` | `src/mcp/app/routers/models.py` | models |  |
| POST | `/updates/dismiss/{update_id}` | `dismiss_model_update` | `src/mcp/app/routers/models.py` | models |  |
| GET | `/claim-accuracy` | `get_claim_accuracy` | `src/mcp/app/routers/observability.py` | observability |  |
| GET | `/cost` | `get_cost_breakdown` | `src/mcp/app/routers/observability.py` | observability |  |
| GET | `/cost-per-query` | `get_cost_per_query` | `src/mcp/app/routers/observability.py` | observability |  |
| GET | `/health-score` | `get_health_score` | `src/mcp/app/routers/observability.py` | observability |  |
| GET | `/metrics` | `get_aggregated_metrics` | `src/mcp/app/routers/observability.py` | observability |  |
| GET | `/metrics/{name}` | `get_metric_timeseries` | `src/mcp/app/routers/observability.py` | observability |  |
| GET | `/quality` | `get_quality_metrics` | `src/mcp/app/routers/observability.py` | observability |  |
| GET | `/queue-depth` | `queue_depth_endpoint` | `src/mcp/app/routers/observability.py` | observability |  |
| GET | `/ragas` | `get_ragas_metrics` | `src/mcp/app/routers/observability.py` | observability |  |
| POST | `/chat` | `chat_completion` | `src/mcp/app/routers/ollama_proxy.py` | ollama |  |
| GET | `/models` | `list_ollama_models` | `src/mcp/app/routers/ollama_proxy.py` | ollama |  |
| POST | `/pull` | `pull_model` | `src/mcp/app/routers/ollama_proxy.py` | ollama |  |
| GET | `/recommendations` | `get_recommendations` | `src/mcp/app/routers/ollama_proxy.py` | ollama |  |
| GET | `` | `list_community_plugins` | `src/mcp/app/routers/plugin_registry.py` | plugin-registry |  |
| GET | `/{name}` | `get_community_plugin` | `src/mcp/app/routers/plugin_registry.py` | plugin-registry |  |
| GET | `/plugins` | `list_plugins` | `src/mcp/app/routers/plugins.py` | plugins |  |
| POST | `/plugins/scan` | `scan_plugins` | `src/mcp/app/routers/plugins.py` | plugins |  |
| GET | `/plugins/{name}` | `get_plugin` | `src/mcp/app/routers/plugins.py` | plugins |  |
| GET | `/plugins/{name}/config` | `get_plugin_config` | `src/mcp/app/routers/plugins.py` | plugins |  |
| PUT | `/plugins/{name}/config` | `update_plugin_config` | `src/mcp/app/routers/plugins.py` | plugins |  |
| POST | `/plugins/{name}/disable` | `disable_plugin` | `src/mcp/app/routers/plugins.py` | plugins |  |
| POST | `/plugins/{name}/enable` | `enable_plugin` | `src/mcp/app/routers/plugins.py` | plugins |  |
| GET | `` | `list_providers` | `src/mcp/app/routers/providers.py` | providers |  |
| GET | `/config` | `get_model_provider_config` | `src/mcp/app/routers/providers.py` | providers |  |
| PUT | `/config` | `update_model_provider_config` | `src/mcp/app/routers/providers.py` | providers |  |
| GET | `/configured` | `list_configured_providers` | `src/mcp/app/routers/providers.py` | providers |  |
| GET | `/credits` | `get_provider_credits` | `src/mcp/app/routers/providers.py` | providers |  |
| GET | `/internal` | `get_internal_provider` | `src/mcp/app/routers/providers.py` | providers |  |
| PUT | `/internal` | `set_internal_provider` | `src/mcp/app/routers/providers.py` | providers |  |
| POST | `/ollama/disable` | `disable_ollama` | `src/mcp/app/routers/providers.py` | providers |  |
| POST | `/ollama/enable` | `enable_ollama` | `src/mcp/app/routers/providers.py` | providers |  |
| GET | `/ollama/recommendations` | `get_ollama_recommendations` | `src/mcp/app/routers/providers.py` | providers |  |
| GET | `/ollama/status` | `get_ollama_status` | `src/mcp/app/routers/providers.py` | providers |  |
| GET | `/routing` | `get_routing_info` | `src/mcp/app/routers/providers.py` | providers |  |
| GET | `/{name}` | `get_provider` | `src/mcp/app/routers/providers.py` | providers |  |
| POST | `/{name}/validate` | `validate_key` | `src/mcp/app/routers/providers.py` | providers |  |
| POST | `/query` | `query_endpoint` | `src/mcp/app/routers/query.py` |  |  |
| POST | `/admin/scan` | `start_scan` | `src/mcp/app/routers/scanner.py` | scanner |  |
| GET | `/admin/scan/preview` | `scan_preview` | `src/mcp/app/routers/scanner.py` | scanner |  |
| POST | `/admin/scan/preview` | `scan_preview_post` | `src/mcp/app/routers/scanner.py` | scanner |  |
| POST | `/admin/scan/reset` | `reset_scan_state` | `src/mcp/app/routers/scanner.py` | scanner |  |
| GET | `/admin/scan/state` | `scan_state` | `src/mcp/app/routers/scanner.py` | scanner |  |
| GET | `/admin/scan/{scan_id}` | `get_scan_progress` | `src/mcp/app/routers/scanner.py` | scanner |  |
| GET | `/collections` | `sdk_collections` | `src/mcp/app/routers/sdk.py` | SDK |  |
| POST | `/hallucination` | `sdk_hallucination` | `src/mcp/app/routers/sdk.py` | SDK |  |
| GET | `/health` | `sdk_health` | `src/mcp/app/routers/sdk.py` | SDK |  |
| GET | `/health/detailed` | `sdk_health_detailed` | `src/mcp/app/routers/sdk.py` | SDK |  |
| POST | `/ingest` | `sdk_ingest` | `src/mcp/app/routers/sdk.py` | SDK |  |
| POST | `/ingest/file` | `sdk_ingest_file` | `src/mcp/app/routers/sdk.py` | SDK |  |
| POST | `/memory/extract` | `sdk_memory_extract` | `src/mcp/app/routers/sdk.py` | SDK |  |
| GET | `/plugins` | `sdk_plugins` | `src/mcp/app/routers/sdk.py` | SDK |  |
| POST | `/query` | `sdk_query` | `src/mcp/app/routers/sdk.py` | SDK |  |
| POST | `/search` | `sdk_search` | `src/mcp/app/routers/sdk.py` | SDK |  |
| GET | `/settings` | `sdk_settings` | `src/mcp/app/routers/sdk.py` | SDK |  |
| GET | `/taxonomy` | `sdk_taxonomy` | `src/mcp/app/routers/sdk.py` | SDK |  |
| GET | `/sdk/v1/openapi.json` | `sdk_openapi` | `src/mcp/app/routers/sdk_openapi.py` | SDK |  |
| GET | `/settings` | `get_settings_endpoint` | `src/mcp/app/routers/settings.py` |  |  |
| PATCH | `/settings` | `update_settings_endpoint` | `src/mcp/app/routers/settings.py` |  |  |
| DELETE | `/settings/private-mode` | `reset_private_mode` | `src/mcp/app/routers/settings.py` |  |  |
| GET | `/settings/private-mode` | `get_private_mode` | `src/mcp/app/routers/settings.py` |  |  |
| POST | `/settings/private-mode` | `set_private_mode` | `src/mcp/app/routers/settings.py` |  |  |
| POST | `/settings/tier` | `set_tier` | `src/mcp/app/routers/settings.py` |  |  |
| GET | `/settings/openrouter-key` | `get_openrouter_key_status` | `src/mcp/app/routers/settings_secrets.py` | settings-secrets |  |
| PUT | `/settings/openrouter-key` | `put_openrouter_key` | `src/mcp/app/routers/settings_secrets.py` | settings-secrets |  |
| POST | `/settings/openrouter-key/test` | `test_openrouter_key` | `src/mcp/app/routers/settings_secrets.py` | settings-secrets |  |
| POST | `/configure` | `configure` | `src/mcp/app/routers/setup.py` | setup |  |
| GET | `/health` | `setup_health` | `src/mcp/app/routers/setup.py` | setup |  |
| POST | `/models/preload` | `models_preload` | `src/mcp/app/routers/setup.py` | setup |  |
| GET | `/models/status` | `models_status` | `src/mcp/app/routers/setup.py` | setup |  |
| POST | `/retest-services` | `retest_services` | `src/mcp/app/routers/setup.py` | setup |  |
| POST | `/retest-verification` | `retest_verification` | `src/mcp/app/routers/setup.py` | setup |  |
| GET | `/status` | `setup_status` | `src/mcp/app/routers/setup.py` | setup |  |
| GET | `/system-check` | `system_check` | `src/mcp/app/routers/setup.py` | setup |  |
| POST | `/validate-key` | `validate_key` | `src/mcp/app/routers/setup.py` | setup |  |
| POST | `/sync/export` | `sync_export_endpoint` | `src/mcp/app/routers/sync.py` |  |  |
| POST | `/sync/import` | `sync_import_endpoint` | `src/mcp/app/routers/sync.py` |  |  |
| GET | `/sync/status` | `sync_status_endpoint` | `src/mcp/app/routers/sync.py` |  |  |
| GET | `/admin/ingest-history` | `get_ingest_history` | `src/mcp/app/routers/system_monitor.py` |  |  |
| GET | `/system/storage` | `get_storage_metrics` | `src/mcp/app/routers/system_monitor.py` |  |  |
| GET | `/tags` | `list_tags_endpoint` | `src/mcp/app/routers/taxonomy.py` |  |  |
| POST | `/tags/merge` | `merge_tags_endpoint` | `src/mcp/app/routers/taxonomy.py` |  |  |
| GET | `/tags/suggest` | `suggest_tags_endpoint` | `src/mcp/app/routers/taxonomy.py` |  |  |
| GET | `/taxonomy` | `get_taxonomy_endpoint` | `src/mcp/app/routers/taxonomy.py` |  |  |
| POST | `/taxonomy/artifact` | `update_artifact_taxonomy_endpoint` | `src/mcp/app/routers/taxonomy.py` |  |  |
| POST | `/taxonomy/domain` | `create_domain_endpoint` | `src/mcp/app/routers/taxonomy.py` |  |  |
| POST | `/taxonomy/subcategory` | `create_subcategory_endpoint` | `src/mcp/app/routers/taxonomy.py` |  |  |
| GET | `/archive/files` | `list_archive_files` | `src/mcp/app/routers/upload.py` |  |  |
| POST | `/upload` | `upload_file_endpoint` | `src/mcp/app/routers/upload.py` |  |  |
| GET | `/upload/supported` | `supported_extensions_endpoint` | `src/mcp/app/routers/upload.py` |  |  |
| GET | `` | `get_user_state_summary` | `src/mcp/app/routers/user_state.py` | user-state |  |
| GET | `/conversations` | `list_conversations` | `src/mcp/app/routers/user_state.py` | user-state |  |
| POST | `/conversations` | `save_conversation` | `src/mcp/app/routers/user_state.py` | user-state |  |
| POST | `/conversations/bulk` | `save_conversations_bulk` | `src/mcp/app/routers/user_state.py` | user-state |  |
| DELETE | `/conversations/{conv_id}` | `remove_conversation` | `src/mcp/app/routers/user_state.py` | user-state |  |
| GET | `/conversations/{conv_id}` | `get_conversation` | `src/mcp/app/routers/user_state.py` | user-state |  |
| PATCH | `/preferences` | `save_preferences` | `src/mcp/app/routers/user_state.py` | user-state |  |
| GET | `` | `list_watched_folders` | `src/mcp/app/routers/watched_folders.py` | watched-folders |  |
| POST | `` | `create_watched_folder` | `src/mcp/app/routers/watched_folders.py` | watched-folders |  |
| DELETE | `/{folder_id}` | `delete_watched_folder` | `src/mcp/app/routers/watched_folders.py` | watched-folders |  |
| GET | `/{folder_id}` | `get_watched_folder` | `src/mcp/app/routers/watched_folders.py` | watched-folders |  |
| PATCH | `/{folder_id}` | `update_watched_folder` | `src/mcp/app/routers/watched_folders.py` | watched-folders |  |
| POST | `/{folder_id}/scan` | `scan_watched_folder` | `src/mcp/app/routers/watched_folders.py` | watched-folders |  |
| GET | `/{folder_id}/status` | `get_folder_status` | `src/mcp/app/routers/watched_folders.py` | watched-folders |  |
| GET | `/webhooks` | `list_subscriptions` | `src/mcp/app/routers/webhook_subscriptions.py` | webhooks |  |
| POST | `/webhooks` | `create_subscription` | `src/mcp/app/routers/webhook_subscriptions.py` | webhooks |  |
| DELETE | `/webhooks/{sub_id}` | `delete_subscription` | `src/mcp/app/routers/webhook_subscriptions.py` | webhooks |  |
| GET | `/webhooks/{sub_id}` | `get_subscription` | `src/mcp/app/routers/webhook_subscriptions.py` | webhooks |  |
| PATCH | `/webhooks/{sub_id}` | `update_subscription` | `src/mcp/app/routers/webhook_subscriptions.py` | webhooks |  |
| GET | `/webhooks/{sub_id}/deliveries` | `list_deliveries` | `src/mcp/app/routers/webhook_subscriptions.py` | webhooks |  |
| GET | `/widget.html` | `widget_page` | `src/mcp/app/routers/widget.py` | Widget |  |
| GET | `/widget.js` | `widget_script` | `src/mcp/app/routers/widget.py` | Widget |  |
| GET | `/widget/config` | `widget_config` | `src/mcp/app/routers/widget.py` | Widget |  |
| GET | `` | `list_workflows` | `src/mcp/app/routers/workflows.py` | workflows |  |
| POST | `` | `create_workflow` | `src/mcp/app/routers/workflows.py` | workflows |  |
| GET | `/templates` | `list_templates` | `src/mcp/app/routers/workflows.py` | workflows |  |
| DELETE | `/{workflow_id}` | `delete_workflow` | `src/mcp/app/routers/workflows.py` | workflows |  |
| GET | `/{workflow_id}` | `get_workflow` | `src/mcp/app/routers/workflows.py` | workflows |  |
| PUT | `/{workflow_id}` | `update_workflow` | `src/mcp/app/routers/workflows.py` | workflows |  |
| POST | `/{workflow_id}/run` | `run_workflow` | `src/mcp/app/routers/workflows.py` | workflows |  |
| GET | `/{workflow_id}/runs` | `list_runs` | `src/mcp/app/routers/workflows.py` | workflows |  |
