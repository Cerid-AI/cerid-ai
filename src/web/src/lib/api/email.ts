// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * IMAP email source REST client. Backs the Sources → Email panel. Mirrors the
 * legacy `/data-sources/email/*` endpoints in
 * src/mcp/app/routers/data_sources.py (the IMAP poller, distinct from the
 * SourceConnector framework). The mailbox is opened read-only; the backend
 * polls it on the SCHEDULE_EMAIL_POLL cadence and dedups via a processed-UID
 * set, so configuring here is enough to ingest mail automatically.
 */

import { mcpUrl, mcpHeaders, extractError } from "./common"

export interface EmailConfig {
  host: string
  port: number
  user: string
  password: string
  folder: string
  poll_interval: number
}

export interface EmailStatus {
  last_poll: string | null
  messages_ingested: number
  errors: string[]
  /** True when a mailbox is configured (Redis or env), even before first poll. */
  configured?: boolean
}

export interface EmailPollResult {
  status: string
  messages: number
  errors?: string[] | null
}

/** Validate IMAP connectivity and persist the config. Throws on bad creds. */
export async function configureEmail(config: EmailConfig): Promise<{ status: string; host: string; user: string }> {
  const r = await fetch(mcpUrl("/data-sources/email/configure").toString(), {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(config),
  })
  if (!r.ok) throw new Error(await extractError(r, `configureEmail failed: ${r.status}`))
  return r.json()
}

export async function fetchEmailStatus(): Promise<EmailStatus> {
  const r = await fetch(mcpUrl("/data-sources/email/status").toString(), { headers: mcpHeaders() })
  if (!r.ok) throw new Error(await extractError(r, `fetchEmailStatus failed: ${r.status}`))
  return r.json()
}

export async function pollEmailNow(): Promise<EmailPollResult> {
  const r = await fetch(mcpUrl("/data-sources/email/poll-now").toString(), {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!r.ok) throw new Error(await extractError(r, `pollEmailNow failed: ${r.status}`))
  return r.json()
}

export async function deleteEmailSource(): Promise<{ status: string }> {
  const r = await fetch(mcpUrl("/data-sources/email").toString(), {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!r.ok) throw new Error(await extractError(r, `deleteEmailSource failed: ${r.status}`))
  return r.json()
}
