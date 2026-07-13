// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { SourceRecord } from "@/lib/api/sources"
import type { ConnectorStatus } from "@/lib/api/connectors"
import type { EmailStatus } from "@/lib/api/email"
import type { AppleBridgeKind } from "./apple-detail"

// Apple bridge rows carry no REST-backing payload; the kind is sufficient
// because AppleDetail receives it directly via the row's `kind` field.
export type AppleRow = { bridgeKind: AppleBridgeKind }

/** EmailRow backing is the EmailStatus snapshot returned by /data-sources/email/status. */
export type EmailRow = EmailStatus

/** Per-connector explainer fields served by /connectors (connectors.py
    ConnectorMeta). Optional so payloads from older backends still parse. */
export interface ConnectorExplainer {
  /** What the connector reads/imports. */
  imports_desc?: string
  /** One-time import vs continuous sync vs on-demand lookup. */
  sync_semantics?: string
  /** Where the data ends up (chat answers, briefs, KB domain…). */
  lands_in?: string
}

export type ConnectorStatusExt = ConnectorStatus & ConnectorExplainer

export interface DisplayRow {
  id: string
  rowType: "source" | "connector" | "email" | "apple"
  kind: string
  displayName: string
  status: "connected" | "paused" | "available" | "error"
  /** Optional secondary line for the list row (e.g. a connector's sync semantics). */
  detail?: string
  backing: SourceRecord | ConnectorStatus | EmailRow | AppleRow
}

export function emailToRow(status: EmailStatus): DisplayRow {
  const hasActivity =
    !!status.configured || !!status.last_poll || (status.messages_ingested ?? 0) > 0

  return {
    id: "email:imap",
    rowType: "email",
    kind: "email",
    displayName: "Email (IMAP)",
    status: hasActivity ? "connected" : "available",
    backing: status,
  }
}

export function sourceToRow(s: SourceRecord): DisplayRow {
  const status = ((): DisplayRow["status"] => {
    switch (s.status) {
      case "connected": return "connected"
      case "paused": return "paused"
      case "error": return "error"
      default: return "available"
    }
  })()

  return {
    id: s.id,
    rowType: "source",
    kind: s.kind,
    displayName: s.display_name,
    status,
    backing: s,
  }
}

export function connectorToRow(c: ConnectorStatusExt): DisplayRow {
  const status = ((): DisplayRow["status"] => {
    if (c.data_source_configured) return "connected"
    if (c.env_complete) return "available"
    return "error"
  })()

  return {
    id: `connector:${c.slug}`,
    rowType: "connector",
    kind: c.slug,
    displayName: c.display_name,
    status,
    // Surface the sync model right in the list so "what will connecting
    // this actually do?" is answered before opening the detail dialog.
    detail: c.sync_semantics,
    backing: c,
  }
}

// ---------------------------------------------------------------------------
// Apple bridge rows (desktop-only)
//
// Returns rows for the three Electron-bridge kinds (notes / mail / imessage).
// Returns [] in browser builds (no window.cerid.appleConnectors).
//
// Status is always "available" — we don't lift per-scan state to the row level;
// that state lives inside AppleDetail (scanned on dialog open). Keeping the
// row status simple avoids a bridge scan on every SourcesConnectors mount.
// ---------------------------------------------------------------------------

const APPLE_BRIDGE_KINDS: Array<{ kind: AppleBridgeKind; displayName: string }> = [
  { kind: "notes", displayName: "Apple Notes" },
  { kind: "mail", displayName: "Apple Mail" },
  { kind: "imessage", displayName: "iMessage" },
]

export function appleRows(): DisplayRow[] {
  if (typeof window === "undefined" || !window.cerid?.appleConnectors) return []
  return APPLE_BRIDGE_KINDS.map(({ kind, displayName }) => ({
    id: `apple:${kind}`,
    rowType: "apple" as const,
    kind,
    displayName,
    status: "available" as const,
    backing: { bridgeKind: kind } as AppleRow,
  }))
}
