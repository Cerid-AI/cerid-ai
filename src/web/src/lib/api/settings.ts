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

// --- Settings ---

export async function fetchSettings(): Promise<ServerSettings> {
  const res = await fetch(`${MCP_BASE}/settings`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Settings fetch failed: ${res.status}`))
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
  return fetch(`${MCP_BASE}/ollama/pull`, {
    method: "POST",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  })
}

export async function disableOllama(): Promise<{ status: string; provider: string }> {
  const res = await fetch(`${MCP_BASE}/providers/ollama/disable`, { method: "POST", headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Disable Ollama failed: ${res.status}`))
  return res.json()
}

// ---------------------------------------------------------------------------
// Watched Folders

export interface WatchedFolder {
  id: string
  path: string
  label: string
  enabled: boolean
  domain_override: string | null
  exclude_patterns: string[]
  search_enabled: boolean
  last_scanned_at: string | null
  stats: { ingested: number; skipped: number; errored: number }
  created_at: string
}

export async function fetchWatchedFolders(): Promise<{ folders: WatchedFolder[]; total: number }> {
  const res = await fetch(`${MCP_BASE}/watched-folders`, { headers: mcpHeaders() })
  if (!res.ok) return { folders: [], total: 0 }
  return res.json()
}

export async function addWatchedFolder(data: { path: string; label?: string; domain_override?: string; search_enabled?: boolean }): Promise<WatchedFolder> {
  const res = await fetch(`${MCP_BASE}/watched-folders`, {
    method: "POST",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(await extractError(res, `Add folder failed: ${res.status}`))
  return res.json()
}

export async function updateWatchedFolder(id: string, data: { enabled?: boolean; label?: string; search_enabled?: boolean; domain_override?: string }): Promise<WatchedFolder> {
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
    method: "PATCH",
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

export async function fetchPlugins(): Promise<PluginListResponse> {
  const res = await fetch(`${MCP_BASE}/plugins`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Fetch plugins failed: ${res.status}`))
  return res.json()
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
  return res.json()
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

export interface ModelComparisonInfo {
  model_id: string
  name: string
  context_length: number | null
  input_cost_per_1m: number
  output_cost_per_1m: number
  deprecated: boolean
  deprecation_info?: { successor?: string; reason?: string; deprecated_date?: string } | null
  top_provider?: Record<string, unknown>
  architecture?: Record<string, unknown>
}

export interface ModelComparisonResponse {
  current: ModelComparisonInfo
  candidate: ModelComparisonInfo
  recommendation: string
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

export async function fetchModelComparison(
  currentModel: string,
  candidateModel: string,
): Promise<ModelComparisonResponse> {
  const params = new URLSearchParams({
    current_model: currentModel,
    candidate_model: candidateModel,
  })
  const res = await fetch(`${MCP_BASE}/models/compare?${params}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Model comparison failed: ${res.status}`))
  return res.json()
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
