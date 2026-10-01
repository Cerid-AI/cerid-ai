// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { fetchIngestLog } from "@/lib/api/kb"
import type { IngestHistoryEntry, IngestLogEntry } from "@/lib/types"

// The audit log carries every kind of event the system records (queries,
// scheduled jobs, rectification, memory writes). Only the two that describe
// something arriving in the corpus belong in an ingestion ledger.
const INGEST_EVENTS: Record<string, IngestHistoryEntry["status"]> = {
  ingest: "success",
  duplicate: "skipped",
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback
}

/** Shape one audit-log row into a settled ingestion entry. */
function toHistoryEntry(entry: IngestLogEntry, index: number): IngestHistoryEntry {
  return {
    id: `${entry.timestamp}-${entry.artifact_id || index}`,
    filename: entry.filename || entry.artifact_id || "untitled",
    source_type: asString(entry.source_type, "upload"),
    domain: entry.domain ?? "",
    status: INGEST_EVENTS[entry.event] ?? "success",
    timestamp: entry.timestamp,
    chunks: typeof entry.chunks === "number" ? entry.chunks : 0,
    error: asString(entry.error),
  }
}

/**
 * Read the ingestion ledger.
 *
 * /ingest_log returns cache.get_log()'s bare list; the {total, entries}
 * wrapper the client type declares was never on the wire. Accept both rather
 * than let a shape mismatch present itself as "nothing was ingested" -- that
 * failure mode is the whole reason this finding exists.
 */
export async function fetchIngestActivity(limit: number): Promise<IngestHistoryEntry[]> {
  const raw = (await fetchIngestLog(limit)) as unknown
  const entries: IngestLogEntry[] = Array.isArray(raw)
    ? (raw as IngestLogEntry[])
    : ((raw as { entries?: IngestLogEntry[] } | null)?.entries ?? [])
  return entries.filter((e) => e.event in INGEST_EVENTS).map(toHistoryEntry)
}
