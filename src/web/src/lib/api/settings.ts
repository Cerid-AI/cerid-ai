// Copyright (c) 2026 Justin Michaels. All rights reserved.
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

// -- Auth API (Phase 31 — multi-user) ----------------------------------------

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
  const res = await fetch(`${MCP_BASE}/setup/configure`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(config),
  })
  if (!res.ok) throw new Error(await extractError(res, `Setup configure failed: ${res.status}`))
  return res.json()
}

export async function fetchSetupHealth(): Promise<SetupHealth> {
  const res = await fetch(`${MCP_BASE}/setup/health`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Setup health check failed: ${res.status}`))
  return res.json()
}

// ---------------------------------------------------------------------------
// OpenRouter Credits
// ---------------------------------------------------------------------------

export async function fetchOpenRouterCredits(): Promise<import("../types").OpenRouterCredits> {
  const res = await fetch(`${MCP_BASE}/providers/openrouter/credits`, {
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

export async function enableOllama(): Promise<{ status: string; provider: string; model: string; url: string }> {
  const res = await fetch(`${MCP_BASE}/providers/ollama/enable`, { method: "POST", headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Enable Ollama failed: ${res.status}`))
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
  const res = await fetch(`${MCP_BASE}/automations/${id}/toggle`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ enabled }),
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
// Observability (Phase 47)
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
// Workflows (Phase 50)
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
