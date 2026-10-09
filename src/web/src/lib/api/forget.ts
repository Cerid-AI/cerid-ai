// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// Forget engine: preview what a conversation produced, or what a selection of
// documents, passages and memories would take with it; move it to the Trash or
// forget it permanently, restore, empty the Trash, and read receipts.

import { MCP_BASE, mcpHeaders, extractError } from "./common"

export type ForgetKind = "conversation" | "artifact" | "chunk" | "memory"
export interface ForgetSubject { kind: ForgetKind; id: string }
export type ForgetMode = "trash" | "permanent"
/** Who asked: the forget dialogs ("api") or the forget assistant ("agent"). Recorded with the forget. */
export type ForgetSource = "api" | "agent"

export interface PreviewItem extends ForgetSubject {
  label: string
  shared_with?: number
  used_by?: number
  default?: "checked" | "unchecked"
  /** Items preview: the document a passage belongs to, its domain, a parent
   *  passage's child count, and a document's passage count. */
  document?: string
  domain?: string
  children?: number
  passages?: number
}
export type PreviewGroupKey =
  | "transcripts" | "memories" | "summary" | "verified_memories" | "cited_documents"
  | "documents" | "passages" | "conversations"
export interface PreviewGroup { key: PreviewGroupKey; default: "always" | "checked" | "unchecked"; items: PreviewItem[] }
export interface ForgetPreview {
  /** The conversation previewed; null for a selection of items. */
  subject: ForgetSubject | null
  title: string
  groups: PreviewGroup[]
  derived_facts: number
  notes?: string[]
  out_of_reach: string[]
}

export interface ForgetReceipt {
  forget_id: string
  at: string
  requested_by: string
  subjects: ForgetSubject[]
  adapters: Record<string, { status: string; removed: number; error?: string }>
  out_of_reach: string[]
}
export interface ForgetResult { forget_id: string; state: "trashed" | "purged" | "trashed_pending"; receipt: ForgetReceipt | null }
export interface TrashGroup {
  forget_id: string
  at: string
  requested_by: string
  purge_started: boolean
  subjects: (ForgetSubject & { label: string })[]
}
export interface ReceiptSummary {
  forget_id: string
  at: string
  requested_by: string
  subjects: Record<string, number>
  status: "done" | "pending"
}

/** A non-2xx answer from the forget routes, with its status, so callers can
 *  tell a refusal (4xx: nothing was forgotten) from a failure worth retrying. */
export class ForgetHttpError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = "ForgetHttpError"
    this.status = status
  }
}

/** A restore after the purge started: the server refuses it with 409. */
export class ForgetConflictError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "ForgetConflictError"
  }
}

async function post<T>(path: string, body?: unknown, fallback = "Request failed"): Promise<T> {
  const res = await fetch(`${MCP_BASE}${path}`, {
    method: "POST",
    headers: mcpHeaders(body === undefined ? undefined : { "Content-Type": "application/json" }),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new ForgetHttpError(res.status, await extractError(res, fallback))
  return res.json()
}

async function get<T>(path: string, fallback: string): Promise<T> {
  const res = await fetch(`${MCP_BASE}${path}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, fallback))
  return res.json()
}

export function previewForget(conversationId: string): Promise<ForgetPreview> {
  return post("/forget/preview", { kind: "conversation", id: conversationId }, "Couldn't load the preview")
}

/** One thing the forget assistant found. A document lists the passages that matched. */
export interface AssistCandidate extends ForgetSubject {
  store: "knowledge_base" | "memories" | "conversations"
  label: string
  excerpt: string
  domain: string
  reason: string
  score: number
  passages: { kind: "chunk"; id: string; excerpt: string }[]
}
export interface AssistGroup { title: string; explanation: string; items: AssistCandidate[] }
export interface AssistResult {
  /** grouped: sorted by a model; needs_consent: the local model is unavailable and
   *  the cloud model may be asked; ungrouped: shown as found, with the reason. */
  status: "grouped" | "needs_consent" | "ungrouped"
  model: "local" | "cloud" | null
  reason: string
  cloud_model: string
  scope: string
  total: number
  groups: AssistGroup[]
}

/** Everything matching a plain-language description, grouped by the local model.
 *  `allowCloud` is the user's consent to group on the cloud model instead. */
export function searchForgetAssist(scope: string, allowCloud = false): Promise<AssistResult> {
  return post("/forget/assist/search", { scope, allow_cloud: allowCloud }, "Search failed")
}

/** What forgetting documents, passages and memories picked from search would remove. */
export function previewForgetItems(subjects: ForgetSubject[]): Promise<ForgetPreview> {
  return post("/forget/preview", { subjects }, "Couldn't load the preview")
}

export function forgetSubjects(
  subjects: ForgetSubject[], mode: ForgetMode, source: ForgetSource = "api",
): Promise<ForgetResult> {
  return post("/forget", { subjects, mode, source }, "Forget failed")
}

export async function restoreForget(forgetId: string): Promise<{ restored: number }> {
  const res = await fetch(`${MCP_BASE}/forget/${encodeURIComponent(forgetId)}/restore`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (res.status === 409) throw new ForgetConflictError(await extractError(res, "Erasing has started"))
  if (!res.ok) throw new Error(await extractError(res, "Restore failed"))
  return res.json()
}

export function emptyTrash(): Promise<{ purged: string[] }> {
  return post("/forget/trash/empty", undefined, "Empty Trash failed")
}

export async function fetchTrash(): Promise<TrashGroup[]> {
  return (await get<{ items: TrashGroup[] }>("/forget/trash", "Couldn't load the Trash")).items
}

export async function fetchReceipts(): Promise<ReceiptSummary[]> {
  return (await get<{ items: ReceiptSummary[] }>("/forget/receipts", "Couldn't load receipts")).items
}

export function fetchReceipt(forgetId: string): Promise<ForgetReceipt> {
  return get(`/forget/receipts/${encodeURIComponent(forgetId)}`, "Couldn't load the receipt")
}
