// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

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
  /** Row is visible but behind the Pro gate — selecting it opens the upgrade
      dialog instead of the detail pane. */
  proLocked?: boolean
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
//
// These three are Pro but, unlike every other Pro connector, nothing
// server-side enforces that: they never touch the plugin loader (which refuses
// pro-tier plugins at community tier), and they ingest through the generic
// /ingest/structured route. Until 2026-08-09 a community desktop user could
// scan and ingest all three. The lock below IS the enforcement — rows stay
// visible (they are the funnel) but route to the upgrade dialog.
// ---------------------------------------------------------------------------

const APPLE_BRIDGE_KINDS: Array<{ kind: AppleBridgeKind; displayName: string }> = [
  { kind: "notes", displayName: "Apple Notes" },
  { kind: "mail", displayName: "Apple Mail" },
  { kind: "imessage", displayName: "iMessage" },
]

// `isLocked` is required, not defaulted: a call site that forgets it should
// fail the build rather than silently ship the connectors unlocked again. It
// is resolved per kind, not per tier, so a server with one of the three flags
// switched off locks that one row and leaves the others alone.
export function appleRows(isLocked: (kind: AppleBridgeKind) => boolean): DisplayRow[] {
  if (typeof window === "undefined" || !window.cerid?.appleConnectors) return []
  return APPLE_BRIDGE_KINDS.map(({ kind, displayName }) => ({
    id: `apple:${kind}`,
    rowType: "apple" as const,
    kind,
    displayName,
    status: "available" as const,
    proLocked: isLocked(kind),
    backing: { bridgeKind: kind } as AppleRow,
  }))
}
