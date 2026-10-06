// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Inbox setup client. The Companion page talks to /inbox; the same
 * operations are what pkb_inbox_apply and pkb_inbox_undo run.
 */

import { mcpHeaders, mcpUrl } from "./common"

export interface InboxAccount {
  provider: string
  address: string
  display_name: string
  included: boolean
  folder_sort: boolean
  auto_apply: string[]
  utilities: string[]
  consent: string
  removed: boolean
  last_read: string
  last_apply: string
  last_rejection: string
}

export interface InboxArtifactRef {
  id: string
  domain?: string
}

export interface InboxDecision {
  id: string
  account_address: string
  source: string
  provider_thread_id: string
  category: string
  utility: string
  action: string
  band: string
  model: string
  status: string
  mailbox_before: string
  rag_artifact_ids: Array<string | InboxArtifactRef>
  draft_body?: string
  classification_reason?: string
}

export interface SenderPin {
  source: string
  sender: string
  category: string
  action: string
}

export interface InboxSetup {
  actions_enabled: boolean
  background_model: string
  chat_model: string
  accounts: InboxAccount[]
  proposals: InboxDecision[]
  recent: InboxDecision[]
  pins: SenderPin[]
}

export interface InboxApplyResult {
  dry_run?: boolean
  results?: Array<{ decision_id: string; ok: boolean; status: string; reason?: string }>
  ok?: boolean
  status?: string
  reason?: string
  decision_id?: string
}

export interface DiscoveredAddresses {
  gmail: string[]
  outlook: string[]
  apple_mail: string[]
  error: string
}

async function read(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(mcpUrl(path).toString(), {
    ...init,
    headers: mcpHeaders(init?.body ? { "Content-Type": "application/json" } : {}),
  })
  if (!response.ok) throw new Error(`inbox request failed: ${response.status}`)
  return response
}

export async function fetchInboxSetup(provider: string): Promise<InboxSetup> {
  const response = await read(`/inbox/setup?provider=${encodeURIComponent(provider)}`)
  return response.json()
}

export async function addInboxAccount(body: {
  provider: string
  address: string
  display_name?: string
}): Promise<InboxAccount> {
  const response = await read("/inbox/accounts", { method: "POST", body: JSON.stringify(body) })
  return response.json()
}

export async function updateInboxAccount(body: {
  provider: string
  address: string
  display_name?: string
  included?: boolean
  folder_sort?: boolean
  auto_apply?: string[]
  utilities?: string[]
}): Promise<InboxAccount> {
  const response = await read("/inbox/accounts", { method: "PATCH", body: JSON.stringify(body) })
  return response.json()
}

export async function removeInboxAccount(provider: string, address: string): Promise<InboxAccount> {
  const response = await read("/inbox/accounts/remove", {
    method: "POST",
    body: JSON.stringify({ provider, address }),
  })
  return response.json()
}

export async function applyInbox(decisionIds: string[], dryRun = true): Promise<InboxApplyResult> {
  const response = await read("/inbox/apply", {
    method: "POST",
    body: JSON.stringify({ decision_ids: decisionIds, dry_run: dryRun }),
  })
  return response.json()
}

export async function undoInbox(decisionId: string, dryRun = true): Promise<InboxApplyResult> {
  const response = await read("/inbox/undo", {
    method: "POST",
    body: JSON.stringify({ decision_id: decisionId, dry_run: dryRun }),
  })
  return response.json()
}

export async function skipInbox(decisionId: string): Promise<InboxApplyResult> {
  const response = await read("/inbox/skip", {
    method: "POST",
    body: JSON.stringify({ decision_id: decisionId }),
  })
  return response.json()
}

export async function discoverInbox(discover: boolean): Promise<DiscoveredAddresses> {
  const response = await read(`/inbox/discovered?discover=${discover ? "true" : "false"}`)
  return response.json()
}
