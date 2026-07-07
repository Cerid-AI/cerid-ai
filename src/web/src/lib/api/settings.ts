// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { MCP_BASE, mcpHeaders, extractError } from "./common"

import type {
  HealthResponse,
  HealthStatusResponse,
  ServerSettings,
  SettingsUpdate,
  SetupStatus,
  KeyValidation,
  SetupConfig,
  SetupHealth,
  SystemCheckResponse,
  Automation,
  AutomationCreate,
  AutomationRun,
  Plugin,
  PluginConfig,
  PluginListResponse,
  AggregatedMetricsResponse,
  TimeSeriesResponse,
  HealthScoreResponse,
  CostBreakdownResponse,
  QualityMetricsResponse,
  Workflow,
  WorkflowCreate,
  WorkflowRun,
  WorkflowListResponse,
  WorkflowTemplate,
} from "../types"

import type { AuthTokens, AuthUser, UsageInfo } from "../types"

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch(`${MCP_BASE}/health`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Health check failed: ${res.status}`))
  return res.json()
}

export async function fetchHealthStatus(): Promise<HealthStatusResponse> {
  const res = await fetch(`${MCP_BASE}/health/status`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error("Health status fetch failed")
  return res.json()
}

// --- Model preload (Workstream E Phase E.6.3) -------------------------------
// Surfaces the CERID_PRELOAD_MODELS choice in the UI: /setup/models/status
// is a non-blocking probe; /setup/models/preload triggers the HuggingFace
// download for the reranker + embedder so users on lean Docker images
// don't hit a silent 5-15s stall on their first semantic query.

export type ModelCacheStatus = {
  repo: string
  // F-07-01: provider per model so the banner can hide when models are
  // served remotely (e.g., "quenchforge"). Absent on older server builds;
  // treat undefined as "local".
  provider?: string
  // F-07-01: false when the model is served remotely (no local cache needed).
  // Absent on older server builds; treat undefined as `true`.
  needs_local_cache?: boolean
  cached: boolean
  files: Record<string, string | null>
  // Workstream E Phase E.6.6: true when a worker thread is currently
  // inside `_load_model()` — drives the first-query notification banner.
  // Absent on older server builds; treat undefined as `false`.
  loading?: boolean
}

export type ModelsStatusResponse = {
  reranker: ModelCacheStatus
  embedder: ModelCacheStatus
}

export type ModelsPreloadResponse = {
  status: "ok" | "partial"
  reranker_status: "loaded" | "failed" | "skipped_server_side" | "remote_provider"
  reranker_provider?: string
  reranker_ms?: number
  reranker_error?: string
  embedder_status: "loaded" | "failed" | "skipped_server_side" | "remote_provider"
  embedder_provider?: string
  embedder_ms?: number
  embedder_error?: string
  total_ms: number
}

export async function fetchModelsStatus(): Promise<ModelsStatusResponse> {
  const res = await fetch(`${MCP_BASE}/setup/models/status`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Models status fetch failed: ${res.status}`))
  return res.json()
}

export async function preloadModels(): Promise<ModelsPreloadResponse> {
  const res = await fetch(`${MCP_BASE}/setup/models/preload`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Model preload failed: ${res.status}`))
  return res.json()
}

// --- Settings ---

export async function fetchSettings(): Promise<ServerSettings> {
  const res = await fetch(`${MCP_BASE}/settings`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Settings fetch failed: ${res.status}`))
  return res.json()
}

// Task 1.3b/1.3c — data-egress transparency panel
export async function fetchEgressReport(): Promise<import("../types").EgressReport> {
  const res = await fetch(`${MCP_BASE}/settings/egress`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Egress report fetch failed: ${res.status}`))
  return res.json()
}

export async function updateSettings(settings: SettingsUpdate): Promise<{ status: string; updated: Record<string, unknown> }> {
  const res = await fetch(`${MCP_BASE}/settings`, {
    method: "PATCH",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(settings),
  })
  if (!res.ok) throw new Error(await extractError(res, `Settings update failed: ${res.status}`))
  return res.json()
}

export async function setTierOverride(tier: string): Promise<{ status: string; tier: string; feature_flags: Record<string, boolean> }> {
  const res = await fetch(`${MCP_BASE}/settings/tier`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ tier }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Tier override failed: ${res.status}`))
  return res.json()
}

// -- Auth API (multi-user) ----------------------------------------------------

export async function authRegister(
  email: string,
  password: string,
  displayName = "",
  tenantName = "",
): Promise<AuthTokens> {
  const res = await fetch(`${MCP_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, display_name: displayName, tenant_name: tenantName }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Registration failed: ${res.status}`))
  return res.json()
}

export async function authLogin(email: string, password: string): Promise<AuthTokens> {
  const res = await fetch(`${MCP_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Login failed: ${res.status}`))
  return res.json()
}

export async function authRefresh(refreshToken: string): Promise<{ access_token: string }> {
  const res = await fetch(`${MCP_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  })
  if (!res.ok) throw new Error(await extractError(res, "Token refresh failed"))
  return res.json()
}

export async function authLogout(refreshToken: string): Promise<void> {
  await fetch(`${MCP_BASE}/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  })
}

export async function authMe(): Promise<AuthUser> {
  const res = await fetch(`${MCP_BASE}/auth/me`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Not authenticated"))
  return res.json()
}

export async function authSetApiKey(apiKey: string): Promise<void> {
  const res = await fetch(`${MCP_BASE}/auth/me/api-key`, {
    method: "PUT",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ api_key: apiKey }),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to save API key"))
}

export async function authDeleteApiKey(): Promise<void> {
  const res = await fetch(`${MCP_BASE}/auth/me/api-key`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to remove API key"))
}

export async function authApiKeyStatus(): Promise<{ has_key: boolean }> {
  const res = await fetch(`${MCP_BASE}/auth/me/api-key/status`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to check API key status"))
  return res.json()
}

export async function authUsage(): Promise<UsageInfo> {
  const res = await fetch(`${MCP_BASE}/auth/me/usage`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch usage"))
  return res.json()
}

// ---------------------------------------------------------------------------
// Setup Wizard (first-run configuration)
// ---------------------------------------------------------------------------

export async function fetchSetupStatus(): Promise<SetupStatus> {
  const res = await fetch(`${MCP_BASE}/setup/status`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Setup status check failed: ${res.status}`))
  return res.json()
}

export async function validateProviderKey(provider: string, apiKey: string): Promise<KeyValidation> {
  const res = await fetch(`${MCP_BASE}/setup/validate-key`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ provider, api_key: apiKey }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Key validation failed: ${res.status}`))
  return res.json()
}

export async function applySetupConfig(config: SetupConfig): Promise<{ success: boolean }> {
  // Transform the keys dict into the backend's ConfigureRequest individual fields,
  // and pass through KB/Ollama fields directly.
  const KEY_FIELD_MAP: Record<string, string> = {
    openrouter: "openrouter_api_key",
    openai: "openai_api_key",
    anthropic: "anthropic_api_key",
    xai: "xai_api_key",
    neo4j: "neo4j_password",
  }

  const payload: Record<string, unknown> = {}

  // Map provider keys to individual backend fields
  if (config.keys) {
    for (const [provider, value] of Object.entries(config.keys)) {
      const field = KEY_FIELD_MAP[provider.toLowerCase()]
      if (field) {
        payload[field] = value
      }
    }
  }

  // Pass through expanded config fields directly
  if (config.archive_path !== undefined) payload.archive_path = config.archive_path
  if (config.domains !== undefined) payload.domains = config.domains
  if (config.lightweight_mode !== undefined) payload.lightweight_mode = config.lightweight_mode
  if (config.watch_folder !== undefined) payload.watch_folder = config.watch_folder
  if (config.ollama_enabled !== undefined) payload.ollama_enabled = config.ollama_enabled
  if (config.ollama_model !== undefined) payload.ollama_model = config.ollama_model

  const res = await fetch(`${MCP_BASE}/setup/configure`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(await extractError(res, `Setup configure failed: ${res.status}`))
  return res.json()
}

export async function fetchSetupHealth(): Promise<SetupHealth> {
  const res = await fetch(`${MCP_BASE}/setup/health`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Setup health check failed: ${res.status}`))
  return res.json()
}

export async function fetchSystemCheck(): Promise<SystemCheckResponse> {
  const res = await fetch(`${MCP_BASE}/setup/system-check?_t=${Date.now()}`, {
    headers: mcpHeaders(),
    cache: "no-store",
  })
  if (!res.ok) throw new Error("System check failed")
  return res.json()
}

/** Reset LLM connection pool and circuit breakers, then re-probe all services.
 *  Non-throwing — callers should treat errors as best-effort. */
export async function retestServices(): Promise<{ status: string; results: Record<string, unknown> }> {
  const res = await fetch(`${MCP_BASE}/setup/retest-services`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Retest services failed: ${res.status}`))
  return res.json()
}

// ---------------------------------------------------------------------------
// OpenRouter Credits
// ---------------------------------------------------------------------------

export async function fetchOpenRouterCredits(): Promise<import("../types").OpenRouterCredits> {
  const res = await fetch(`${MCP_BASE}/providers/credits`, {
    headers: mcpHeaders(),
  })
  if (!res.ok) return { available: false, error: `HTTP ${res.status}` }
  return res.json()
}

export async function fetchProviderCredits(): Promise<import("../types").ProviderCredits> {
  const res = await fetch(`${MCP_BASE}/providers/credits`, {
    headers: mcpHeaders(),
  })
  if (!res.ok) return { configured: false }
  return res.json()
}

// ---------------------------------------------------------------------------
// Ollama / Internal LLM

export async function fetchOllamaStatus(): Promise<import("../types").OllamaStatus> {
  const res = await fetch(`${MCP_BASE}/providers/ollama/status`, { headers: mcpHeaders() })
  if (!res.ok) return { enabled: false, url: "", reachable: false, models: [], default_model: "", default_model_installed: false }
  return res.json()
}

export async function enableOllama(model?: string): Promise<{ status: string; provider: string; model: string; url: string }> {
  const res = await fetch(`${MCP_BASE}/providers/ollama/enable`, {
    method: "POST",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(model ? { model } : {}),
  })
  if (!res.ok) throw new Error(await extractError(res, `Enable Ollama failed: ${res.status}`))
  return res.json()
}

export async function fetchOllamaRecommendations(): Promise<import("../types").OllamaRecommendations> {
  const res = await fetch(`${MCP_BASE}/providers/ollama/recommendations`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`Recommendations fetch failed: ${res.status}`)
  return res.json()
}

export async function pullOllamaModel(model: string): Promise<Response> {
  const res = await fetch(`${MCP_BASE}/ollama/pull`, {
    method: "POST",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  })
  // The backend returns HTTP 200 with an SSE/JSON body even when pull is
  // unsupported (e.g. Quenchforge: {"status":"not_implemented","error":...}).
  // Surface that as a thrown error so callers don't treat it as success.
  const text = await res.clone().text()
  if (text.includes('"status":"not_implemented"') || text.includes('"status": "not_implemented"')) {
    let message = "Model pull is not supported by this backend."
    const match = text.match(/"error"\s*:\s*"((?:[^"\\]|\\.)*)"/)
    if (match?.[1]) message = match[1].replace(/\\"/g, '"')
    throw new Error(message)
  }
  return res
}

export async function disableOllama(): Promise<{ status: string; provider: string }> {
  const res = await fetch(`${MCP_BASE}/providers/ollama/disable`, { method: "POST", headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Disable Ollama failed: ${res.status}`))
  return res.json()
}

// ---------------------------------------------------------------------------
// Watched Folders

export interface VaultConfig {
  mocs_folders?: string[]
  daily_folders?: string[]
  templates_folders?: string[]
  attachments_folders?: string[]
  skip_folders?: string[]
  default_domain?: string
}

export interface WatchedFolder {
  id: string
  path: string
  label: string
  enabled: boolean
  domain_override: string | null
  exclude_patterns: string[]
  search_enabled: boolean
  is_vault?: boolean
  vault_config?: VaultConfig | null
  last_scanned_at: string | null
  stats: { ingested: number; skipped: number; errored: number }
  created_at: string
}

export interface VaultProfileResponse {
  is_vault: boolean
  yaml_present: boolean
  profile: {
    root_path: string
    mocs_folders: string[]
    daily_folders: string[]
    templates_folders: string[]
    attachments_folders: string[]
    skip_folders: string[]
    default_domain: string
  }
}

export async function fetchWatchedFolders(): Promise<{ folders: WatchedFolder[]; total: number }> {
  const res = await fetch(`${MCP_BASE}/watched-folders`, { headers: mcpHeaders() })
  if (!res.ok) return { folders: [], total: 0 }
  return res.json()
}

export async function addWatchedFolder(data: { path: string; label?: string; domain_override?: string; search_enabled?: boolean; is_vault?: boolean; vault_config?: VaultConfig | null }): Promise<WatchedFolder> {
  const res = await fetch(`${MCP_BASE}/watched-folders`, {
    method: "POST",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(await extractError(res, `Add folder failed: ${res.status}`))
  return res.json()
}

export async function updateWatchedFolder(id: string, data: { enabled?: boolean; label?: string; search_enabled?: boolean; domain_override?: string; is_vault?: boolean; vault_config?: VaultConfig | null }): Promise<WatchedFolder> {
  const res = await fetch(`${MCP_BASE}/watched-folders/${id}`, {
    method: "PATCH",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(await extractError(res, `Update folder failed: ${res.status}`))
  return res.json()
}

export async function removeWatchedFolder(id: string): Promise<void> {
  const res = await fetch(`${MCP_BASE}/watched-folders/${id}`, { method: "DELETE", headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Remove folder failed: ${res.status}`))
}

export async function scanWatchedFolder(id: string): Promise<{ status: string }> {
  const res = await fetch(`${MCP_BASE}/watched-folders/${id}/scan`, { method: "POST", headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Scan failed: ${res.status}`))
  return res.json()
}

export async function fetchVaultProfile(id: string): Promise<VaultProfileResponse | null> {
  const res = await fetch(`${MCP_BASE}/watched-folders/${id}/vault-profile`, { headers: mcpHeaders() })
  if (!res.ok) return null
  return res.json()
}

export async function fetchInternalProvider(): Promise<{ provider: string; model: string; intelligence_model: string; ollama_available: boolean }> {
  const res = await fetch(`${MCP_BASE}/providers/internal`, { headers: mcpHeaders() })
  if (!res.ok) return { provider: "bifrost", model: "", intelligence_model: "", ollama_available: false }
  return res.json()
}

// ---------------------------------------------------------------------------
// Data Sources
// ---------------------------------------------------------------------------

export async function fetchDataSources(): Promise<{ sources: Array<{ name: string; description: string; enabled: boolean; configured: boolean; requires_api_key: boolean; api_key_env_var: string; domains: string[] }>; total: number }> {
  const res = await fetch(`${MCP_BASE}/data-sources`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error("Failed to fetch data sources")
  return res.json()
}

export async function enableDataSource(name: string): Promise<void> {
  await fetch(`${MCP_BASE}/data-sources/${name}/enable`, { method: "POST", headers: mcpHeaders() })
}

export async function disableDataSource(name: string): Promise<void> {
  await fetch(`${MCP_BASE}/data-sources/${name}/disable`, { method: "POST", headers: mcpHeaders() })
}

// ---------------------------------------------------------------------------
// Automations
// ---------------------------------------------------------------------------

export async function fetchAutomations(): Promise<Automation[]> {
  const res = await fetch(`${MCP_BASE}/automations`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Fetch automations failed: ${res.status}`))
  const raw = await res.json()
  return Array.isArray(raw) ? raw : []
}

export async function createAutomation(data: AutomationCreate): Promise<Automation> {
  const res = await fetch(`${MCP_BASE}/automations`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(await extractError(res, `Create automation failed: ${res.status}`))
  return res.json()
}

export async function updateAutomation(id: string, data: Partial<AutomationCreate>): Promise<Automation> {
  const res = await fetch(`${MCP_BASE}/automations/${id}`, {
    method: "PUT",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(await extractError(res, `Update automation failed: ${res.status}`))
  return res.json()
}

export async function deleteAutomation(id: string): Promise<void> {
  const res = await fetch(`${MCP_BASE}/automations/${id}`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Delete automation failed: ${res.status}`))
}

export async function toggleAutomation(id: string, enabled: boolean): Promise<void> {
  const action = enabled ? "enable" : "disable"
  const res = await fetch(`${MCP_BASE}/automations/${id}/${action}`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Toggle automation failed: ${res.status}`))
}

export async function runAutomation(id: string): Promise<AutomationRun> {
  const res = await fetch(`${MCP_BASE}/automations/${id}/run`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Run automation failed: ${res.status}`))
  return res.json()
}

export async function fetchAutomationHistory(id: string): Promise<AutomationRun[]> {
  const res = await fetch(`${MCP_BASE}/automations/${id}/history`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Fetch automation history failed: ${res.status}`))
  return res.json()
}

export async function fetchAutomationPresets(): Promise<Record<string, { label: string; cron: string }>> {
  const res = await fetch(`${MCP_BASE}/automations/presets`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Fetch automation presets failed: ${res.status}`))
  return res.json()
}

// --- Plugins ---

/**
 * The backend returns `plugins` as a dict keyed by id, and individual plugins
 * may omit their array fields. Normalize to the declared PluginListResponse
 * shape (array + guaranteed string[] fields) so every consumer can iterate and
 * call `.includes()` safely — fixes the Connectors/Plugins render crashes at
 * the API boundary instead of relying on per-consumer guards.
 */
function normalizePluginList(data: { plugins?: unknown; total?: number }): PluginListResponse {
  const rawList = Array.isArray(data.plugins)
    ? data.plugins
    : Object.values((data.plugins ?? {}) as Record<string, unknown>)
  const plugins = rawList.map((entry) => {
    const p = entry as Partial<Plugin>
    return {
      ...p,
      file_types: Array.isArray(p.file_types) ? p.file_types : [],
      capabilities: Array.isArray(p.capabilities) ? p.capabilities : [],
    } as Plugin
  })
  return { plugins, total: typeof data.total === "number" ? data.total : plugins.length }
}

export async function fetchPlugins(): Promise<PluginListResponse> {
  const res = await fetch(`${MCP_BASE}/plugins`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Fetch plugins failed: ${res.status}`))
  return normalizePluginList(await res.json())
}

export async function fetchPlugin(name: string): Promise<Plugin> {
  const res = await fetch(`${MCP_BASE}/plugins/${encodeURIComponent(name)}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Fetch plugin failed: ${res.status}`))
  return res.json()
}

export async function enablePlugin(name: string): Promise<Plugin> {
  const res = await fetch(`${MCP_BASE}/plugins/${encodeURIComponent(name)}/enable`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Enable plugin failed: ${res.status}`))
  return res.json()
}

export async function disablePlugin(name: string): Promise<Plugin> {
  const res = await fetch(`${MCP_BASE}/plugins/${encodeURIComponent(name)}/disable`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Disable plugin failed: ${res.status}`))
  return res.json()
}

export async function getPluginConfig(name: string): Promise<PluginConfig> {
  const res = await fetch(`${MCP_BASE}/plugins/${encodeURIComponent(name)}/config`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Get plugin config failed: ${res.status}`))
  return res.json()
}

export async function updatePluginConfig(name: string, config: PluginConfig): Promise<PluginConfig> {
  const res = await fetch(`${MCP_BASE}/plugins/${encodeURIComponent(name)}/config`, {
    method: "PUT",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(config),
  })
  if (!res.ok) throw new Error(await extractError(res, `Update plugin config failed: ${res.status}`))
  return res.json()
}

export async function scanPlugins(): Promise<PluginListResponse> {
  const res = await fetch(`${MCP_BASE}/plugins/scan`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Scan plugins failed: ${res.status}`))
  return normalizePluginList(await res.json())
}

// ---------------------------------------------------------------------------
// Observability
// ---------------------------------------------------------------------------

export async function fetchObservabilityMetrics(windowMinutes = 60): Promise<AggregatedMetricsResponse> {
  const res = await fetch(`${MCP_BASE}/observability/metrics?window_minutes=${windowMinutes}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Observability metrics fetch failed: ${res.status}`))
  return res.json()
}

export async function fetchObservabilityTimeSeries(name: string, windowMinutes = 60): Promise<TimeSeriesResponse> {
  const res = await fetch(`${MCP_BASE}/observability/metrics/${encodeURIComponent(name)}?window_minutes=${windowMinutes}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Metric time series fetch failed: ${res.status}`))
  return res.json()
}

export async function fetchObservabilityHealthScore(windowMinutes = 60): Promise<HealthScoreResponse> {
  const res = await fetch(`${MCP_BASE}/observability/health-score?window_minutes=${windowMinutes}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Health score fetch failed: ${res.status}`))
  return res.json()
}

export async function fetchObservabilityCost(windowMinutes = 60): Promise<CostBreakdownResponse> {
  const res = await fetch(`${MCP_BASE}/observability/cost?window_minutes=${windowMinutes}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Cost breakdown fetch failed: ${res.status}`))
  return res.json()
}

export async function fetchObservabilityQuality(windowMinutes = 60): Promise<QualityMetricsResponse> {
  const res = await fetch(`${MCP_BASE}/observability/quality?window_minutes=${windowMinutes}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Quality metrics fetch failed: ${res.status}`))
  return res.json()
}

// ---------------------------------------------------------------------------
// Workflows
// ---------------------------------------------------------------------------

export async function fetchWorkflows(): Promise<WorkflowListResponse> {
  const res = await fetch(`${MCP_BASE}/workflows`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Fetch workflows failed: ${res.status}`))
  return res.json()
}

export async function fetchWorkflow(id: string): Promise<Workflow> {
  const res = await fetch(`${MCP_BASE}/workflows/${id}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Fetch workflow failed: ${res.status}`))
  return res.json()
}

export async function createWorkflow(data: WorkflowCreate): Promise<Workflow> {
  const res = await fetch(`${MCP_BASE}/workflows`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(await extractError(res, `Create workflow failed: ${res.status}`))
  return res.json()
}

export async function updateWorkflow(id: string, data: Partial<WorkflowCreate>): Promise<Workflow> {
  const res = await fetch(`${MCP_BASE}/workflows/${id}`, {
    method: "PUT",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(await extractError(res, `Update workflow failed: ${res.status}`))
  return res.json()
}

export async function deleteWorkflow(id: string): Promise<void> {
  const res = await fetch(`${MCP_BASE}/workflows/${id}`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Delete workflow failed: ${res.status}`))
}

export async function runWorkflow(id: string, input?: Record<string, unknown>): Promise<WorkflowRun> {
  const res = await fetch(`${MCP_BASE}/workflows/${id}/run`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(input ?? {}),
  })
  if (!res.ok) throw new Error(await extractError(res, `Run workflow failed: ${res.status}`))
  return res.json()
}

export async function fetchWorkflowRuns(id: string, limit = 20): Promise<WorkflowRun[]> {
  const res = await fetch(`${MCP_BASE}/workflows/${id}/runs?limit=${limit}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Fetch workflow runs failed: ${res.status}`))
  const raw = await res.json()
  return Array.isArray(raw) ? raw : []
}

export async function fetchWorkflowTemplates(): Promise<WorkflowTemplate[]> {
  const res = await fetch(`${MCP_BASE}/workflows/templates`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Fetch workflow templates failed: ${res.status}`))
  return res.json()
}

// -- Model Updates -----------------------------------------------------------

export interface ModelUpdateEntry {
  id: string
  name?: string
  context_length?: number | null
  pricing?: { prompt?: string; completion?: string }
}

export interface ModelUpdatesResponse {
  new: ModelUpdateEntry[]
  deprecated: ModelUpdateEntry[]
  last_checked: string | null
  catalog_size?: number
}

export interface ModelUpdateItem {
  update_id: string
  model_id: string
  update_type: "new" | "deprecated" | "price_change"
  details: Record<string, unknown>
  detected_at: string
}

export interface ModelUpdatesFullResponse {
  updates: ModelUpdateItem[]
  last_checked: string | null
  catalog_size: number
}

export async function fetchModelUpdates(): Promise<ModelUpdatesResponse> {
  const res = await fetch(`${MCP_BASE}/models/updates`, { headers: mcpHeaders() })
  if (!res.ok) return { new: [], deprecated: [], last_checked: null }
  return res.json()
}

export async function fetchModelUpdatesFull(): Promise<ModelUpdatesFullResponse> {
  const res = await fetch(`${MCP_BASE}/models/updates`, { headers: mcpHeaders() })
  if (!res.ok) return { updates: [], last_checked: null, catalog_size: 0 }
  return res.json()
}

export async function triggerModelUpdateCheck(): Promise<{
  success: boolean
  new_count: number
  deprecated_count: number
  last_checked: string
}> {
  const res = await fetch(`${MCP_BASE}/models/updates/check`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Model update check failed: ${res.status}`))
  return res.json()
}

export async function dismissModelUpdate(updateId: string): Promise<void> {
  const res = await fetch(`${MCP_BASE}/models/updates/dismiss/${encodeURIComponent(updateId)}`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Dismiss failed: ${res.status}`))
}

// ---------------------------------------------------------------------------
// Private Mode
// ---------------------------------------------------------------------------

export async function fetchPrivateMode(): Promise<{ enabled: boolean; level: number }> {
  const res = await fetch(`${MCP_BASE}/settings/private-mode`, { headers: mcpHeaders() })
  if (!res.ok) return { enabled: false, level: 0 }
  return res.json()
}

export async function enablePrivateMode(level: number = 1): Promise<void> {
  await fetch(`${MCP_BASE}/settings/private-mode`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ level }),
  })
}

export async function disablePrivateMode(clearCache: boolean = false): Promise<void> {
  await fetch(`${MCP_BASE}/settings/private-mode`, {
    method: "DELETE",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ clear_cache: clearCache }),
  })
}

/**
 * L4 session-wipe (Cycle 3.2 / v0.93.5).  Called from a
 * ``beforeunload`` handler via ``navigator.sendBeacon()`` when L4 is
 * active, so the backend's ephemeral-state wipe completes even when
 * the page is unloading.
 *
 * ``sendBeacon`` doesn't accept custom headers, so the api-key header
 * is omitted on this call.  The endpoint is intentionally
 * unauthenticated for this reason — the worst case is a stray POST
 * clearing the private-mode flag, which is the same state any caller
 * can produce by hitting ``DELETE /settings/private-mode`` anyway.
 */
export function wipePrivateSession(conversationId: string): void {
  if (typeof navigator === "undefined" || typeof navigator.sendBeacon !== "function") {
    // Fallback for jsdom / SSR — fire-and-forget fetch.
    void fetch(`${MCP_BASE}/settings/private-mode/session-wipe`, {
      method: "POST",
      headers: mcpHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ conversation_id: conversationId }),
      keepalive: true,
    }).catch(() => { /* noop — best-effort */ })
    return
  }
  const blob = new Blob([JSON.stringify({ conversation_id: conversationId })], {
    type: "application/json",
  })
  navigator.sendBeacon(`${MCP_BASE}/settings/private-mode/session-wipe`, blob)
}

// ---------------------------------------------------------------------------
// Storage Monitoring
// ---------------------------------------------------------------------------

export async function fetchStorageMetrics(): Promise<import("../types").StorageMetrics> {
  const res = await fetch(`${MCP_BASE}/system/storage`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`Storage metrics fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchIngestHistory(
  limit = 50,
  cursor?: string,
): Promise<import("../types").IngestHistoryResponse> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (cursor) params.set("offset", cursor)
  const res = await fetch(`${MCP_BASE}/admin/ingest-history?${params}`, { headers: mcpHeaders() })
  if (!res.ok) return { items: [], total: 0, next_cursor: null }
  return res.json()
}

// ---------------------------------------------------------------------------
// Write-only OpenRouter key API (R4-1)
// The raw key value NEVER appears in any response body.
// ---------------------------------------------------------------------------

export interface OpenRouterKeyStatus {
  configured: boolean
  last4: string | null
  updated_at: string | null
}

export interface OpenRouterKeyTestResult {
  valid: boolean
  credits_remaining: number | null
  error: string | null
}

export async function fetchOpenRouterKeyStatus(): Promise<OpenRouterKeyStatus> {
  const res = await fetch(`${MCP_BASE}/settings/openrouter-key`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch OpenRouter key status"))
  return res.json()
}

export async function putOpenRouterKey(api_key: string): Promise<OpenRouterKeyStatus> {
  const res = await fetch(`${MCP_BASE}/settings/openrouter-key`, {
    method: "PUT",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ api_key }),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to save OpenRouter key"))
  return res.json()
}

export async function testOpenRouterKey(api_key?: string): Promise<OpenRouterKeyTestResult> {
  const res = await fetch(`${MCP_BASE}/settings/openrouter-key/test`, {
    method: "POST",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(api_key ? { api_key } : {}),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to test OpenRouter key"))
  return res.json()
}

// ---------------------------------------------------------------------------
// HuggingFace token API (Phase E — gates pyannote diarization models)
// ---------------------------------------------------------------------------

export interface HFTokenStatus {
  configured: boolean
  last4: string | null
  updated_at: string | null
  model_access: Record<string, boolean> | null
}

export interface HFTokenTestResult {
  valid: boolean
  gated_model_access: Record<string, boolean> | null
  error: string | null
}

export async function fetchHFTokenStatus(): Promise<HFTokenStatus> {
  const res = await fetch(`${MCP_BASE}/settings/hf-token`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch HF token status"))
  return res.json()
}

export async function putHFToken(token: string): Promise<HFTokenStatus> {
  const res = await fetch(`${MCP_BASE}/settings/hf-token`, {
    method: "PUT",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to save HF token"))
  return res.json()
}

export async function testHFToken(token?: string): Promise<HFTokenTestResult> {
  const res = await fetch(`${MCP_BASE}/settings/hf-token/test`, {
    method: "POST",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(token ? { token } : {}),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to test HF token"))
  return res.json()
}

// ---------------------------------------------------------------------------
// Whisper model download manager API (Phase E Day 3)
// ---------------------------------------------------------------------------

export interface WhisperModelInfo {
  id: string
  filename: string
  size_mb: number
  rtf_estimate: number
  quality: string
  description: string
  cached: boolean
  cached_size_bytes: number | null
}

export interface WhisperModelList {
  models: WhisperModelInfo[]
  cache_dir: string
  current_default: string
}

export interface WhisperDownloadStatus {
  download_id: string
  model_id: string
  state: "pending" | "downloading" | "completed" | "failed" | "cancelled"
  bytes_downloaded: number
  bytes_total: number | null
  error: string | null
}

export async function fetchWhisperModels(): Promise<WhisperModelList> {
  const res = await fetch(`${MCP_BASE}/settings/whisper/models`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch Whisper models"))
  return res.json()
}

export async function startWhisperDownload(model_id: string): Promise<{ download_id: string; model_id: string }> {
  const res = await fetch(`${MCP_BASE}/settings/whisper/download`, {
    method: "POST",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ model_id }),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to start download"))
  return res.json()
}

export async function getWhisperDownloadStatus(download_id: string): Promise<WhisperDownloadStatus> {
  const res = await fetch(`${MCP_BASE}/settings/whisper/download/${download_id}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch download status"))
  return res.json()
}

export async function cancelWhisperDownload(download_id: string): Promise<WhisperDownloadStatus> {
  const res = await fetch(`${MCP_BASE}/settings/whisper/download/${download_id}`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to cancel download"))
  return res.json()
}

export async function deleteWhisperModel(model_id: string): Promise<{ deleted: boolean }> {
  const res = await fetch(`${MCP_BASE}/settings/whisper/models/${model_id}`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to delete model"))
  return res.json()
}

// ---------------------------------------------------------------------------
// Custom Smart RAG weights (Phase I)
// ---------------------------------------------------------------------------

export interface RagWeightMap {
  weights: Record<string, number>
  user_scope: string
  feature_enabled: boolean
}

export interface RagSource {
  name: string
  kind: "data_source" | "kb_domain"
  description: string
  default_enabled: boolean
  current_weight: number
}

export interface RagSourcesList {
  sources: RagSource[]
  min_weight: number
  max_weight: number
  default_weight: number
  feature_enabled: boolean
}

export async function fetchRagWeights(): Promise<RagWeightMap> {
  const res = await fetch(`${MCP_BASE}/settings/rag/weights`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch RAG weights"))
  return res.json()
}

export async function fetchRagSources(): Promise<RagSourcesList> {
  const res = await fetch(`${MCP_BASE}/settings/rag/weights/sources`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch RAG sources"))
  return res.json()
}

export async function putRagWeights(weights: Record<string, number>): Promise<RagWeightMap> {
  const res = await fetch(`${MCP_BASE}/settings/rag/weights`, {
    method: "PUT",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ weights }),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to save RAG weights"))
  return res.json()
}

export async function resetRagWeights(): Promise<RagWeightMap> {
  const res = await fetch(`${MCP_BASE}/settings/rag/weights`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to reset RAG weights"))
  return res.json()
}

// ---------------------------------------------------------------------------
// Pro-tier feature automations (UX consolidation)
// ---------------------------------------------------------------------------

export interface CadencePreset {
  label: string
  cron: string
}

export interface AutomationState {
  feature: string
  display_name: string
  description: string
  feature_flag: string
  feature_flag_enabled: boolean
  enabled: boolean
  schedule: string
  default_schedule: string
  cadence_presets: CadencePreset[]
}

export interface RunNowResponse {
  feature: string
  triggered: boolean
  detail: string
  result: Record<string, unknown> | null
}

export async function listProAutomations(): Promise<AutomationState[]> {
  const res = await fetch(`${MCP_BASE}/settings/pro-automations`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch automations"))
  const body = await res.json()
  return Array.isArray(body.automations) ? body.automations : []
}

export async function updateProAutomation(
  name: string,
  update: { enabled?: boolean; schedule?: string },
): Promise<AutomationState> {
  const res = await fetch(`${MCP_BASE}/settings/pro-automations/${name}`, {
    method: "PUT",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(update),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to update automation"))
  return res.json()
}

export async function resetProAutomation(name: string): Promise<AutomationState> {
  const res = await fetch(`${MCP_BASE}/settings/pro-automations/${name}`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to reset automation"))
  return res.json()
}

export async function runProAutomationNow(name: string): Promise<RunNowResponse> {
  const res = await fetch(`${MCP_BASE}/settings/pro-automations/${name}/run-now`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to trigger automation"))
  return res.json()
}

// ---------------------------------------------------------------------------
// Brief scheduler settings (RAG C3.4)
// ---------------------------------------------------------------------------

export interface BriefSettings {
  write_to_vault: boolean
  vault_id: string | null
  vault_folder: string
}

export async function fetchBriefSettings(): Promise<BriefSettings> {
  const res = await fetch(`${MCP_BASE}/briefs/settings`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch brief settings"))
  return res.json()
}

export async function updateBriefSettings(body: BriefSettings): Promise<BriefSettings> {
  const res = await fetch(`${MCP_BASE}/briefs/settings`, {
    method: "PUT",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to update brief settings"))
  return res.json()
}

// ── Model compatibility doctor (GET /models/doctor) ──────────────────────────
// Hardware-aware audit: are the configured models the most capable ones that
// actually run on this platform, and are they current? Consumed by the
// Settings → Models UX and the setup wizard's backend step.

export interface ModelDoctorFinding {
  kind: "incompatible" | "dead_pin" | "local_currency"
  severity: "error" | "warn" | "info"
  role: string
  model: string
  detail: string
}

export interface ModelDoctorUpgrade {
  model: string
  why: string
  validate: string
}

export interface ModelDoctorReport {
  hardware_profile: string
  ok: boolean
  findings: ModelDoctorFinding[]
  known_good_local: Record<string, string>
  candidate_upgrades: Record<string, ModelDoctorUpgrade[]>
  catalog_size: number
}

export async function fetchModelDoctor(): Promise<ModelDoctorReport> {
  const res = await fetch(`${MCP_BASE}/models/doctor`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`model doctor request failed: ${res.status}`)
  return res.json()
}
