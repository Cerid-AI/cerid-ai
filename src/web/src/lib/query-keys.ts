// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Centralized React Query key registry.
 *
 * Every `useQuery` / `invalidateQueries` call should reference a key from
 * this module to avoid string duplication and cache-key drift.
 */

export const QUERY_KEYS = {
  // -- KB & Retrieval --
  artifacts: (domain?: string) => ["artifacts", domain ?? "all"] as const,
  kbSearch: (query: string, domain?: string) => ["kb-search", query, domain ?? "all"] as const,
  kbQuery: (query: string, domainKey: string, contextCount: number) =>
    ["kb-query", query, domainKey, contextCount] as const,
  orchestratedQuery: (query: string, ragMode: string, domainKey: string, contextCount: number) =>
    ["orchestrated-query", query, ragMode, domainKey, contextCount] as const,
  artifactDetail: (id: string) => ["artifact-detail", id] as const,
  related: (id: string) => ["related", id] as const,

  // -- Taxonomy & Tags --
  taxonomy: () => ["taxonomy"] as const,
  tagSuggestions: (domain: string, prefix: string) => ["tag-suggestions", domain, prefix] as const,
  allTags: () => ["all-tags"] as const,

  // -- Monitoring --
  maintenance: () => ["maintenance"] as const,
  ingestLog: () => ["ingest-log"] as const,
  scheduler: () => ["scheduler"] as const,
  digest: (hours: number) => ["digest", hours] as const,
  observabilityMetrics: (windowMinutes: number) => ["observability-metrics", windowMinutes] as const,
  observabilityHealth: (windowMinutes: number) => ["observability-health", windowMinutes] as const,

  // -- Health & Status --
  health: () => ["health"] as const,
  healthStatus: () => ["health-status"] as const,
  healthOllamaBanner: () => ["health-ollama-banner"] as const,

  // -- Settings & Providers --
  providerCredits: () => ["provider-credits"] as const,
  ollamaStatus: () => ["ollama-status"] as const,
  dataSources: () => ["data-sources"] as const,

  // -- Audit --
  audit: (hours: number, activeReports: string) => ["audit", hours, activeReports] as const,
} as const
