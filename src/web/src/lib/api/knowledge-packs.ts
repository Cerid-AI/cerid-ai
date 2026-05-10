// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { MCP_BASE, mcpHeaders, extractError } from "./common"

export interface KnowledgePackSummary {
  id: string
  name: string
  version: string
  description: string
  domain: string
  sub_category: string
  tags: string[]
  license: string
  size_bytes: number
  artifact_count: number
  download_url: string
  sha256: string
  provenance: Record<string, string>
}

export interface KnowledgePackRegistryResponse {
  schema_version: number
  packs_by_domain: Record<string, KnowledgePackSummary[]>
}

export interface InstalledKnowledgePack {
  pack_id: string
  version: string
  installed_at: string
  domain: string
  sha256: string
  artifact_count: number
}

export interface InstalledKnowledgePacksResponse {
  schema_version: number
  packs: InstalledKnowledgePack[]
}

export interface InstallKnowledgePackResponse {
  pack_id: string
  version: string
  installed_at: string
  domain: string
  artifact_count: number
}

export interface UninstallKnowledgePackResponse {
  pack_id: string
  status: string
  removed: number
  missing: number
}

/** Fetch the available knowledge-pack registry, grouped by target domain. */
export async function fetchKnowledgePackRegistry(): Promise<KnowledgePackRegistryResponse> {
  const res = await fetch(`${MCP_BASE}/knowledge_packs/registry`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Registry fetch failed: ${res.status}`))
  return res.json()
}

/** Fetch the packs already installed in this KB. */
export async function fetchInstalledKnowledgePacks(): Promise<InstalledKnowledgePacksResponse> {
  const res = await fetch(`${MCP_BASE}/knowledge_packs/installed`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Installed-packs fetch failed: ${res.status}`))
  return res.json()
}

/** Install a pack by id. Idempotent at the same version. */
export async function installKnowledgePack(packId: string): Promise<InstallKnowledgePackResponse> {
  const res = await fetch(
    `${MCP_BASE}/knowledge_packs/${encodeURIComponent(packId)}/install`,
    { method: "POST", headers: mcpHeaders() },
  )
  if (!res.ok) throw new Error(await extractError(res, `Install failed: ${res.status}`))
  return res.json()
}

/** Uninstall a pack by id; removes ingested artifacts from Neo4j + chromadb. */
export async function uninstallKnowledgePack(packId: string): Promise<UninstallKnowledgePackResponse> {
  const res = await fetch(
    `${MCP_BASE}/knowledge_packs/${encodeURIComponent(packId)}`,
    { method: "DELETE", headers: mcpHeaders() },
  )
  if (!res.ok) throw new Error(await extractError(res, `Uninstall failed: ${res.status}`))
  return res.json()
}
