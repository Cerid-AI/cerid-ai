// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Briefs read API client (Task 2.2).
 *
 * Wraps GET /briefs?kind=daily|weekly&limit=N. The list already returns
 * fully hydrated BriefView objects, so there is no client wrapper for the
 * backend's GET /briefs/{id} — the pane resolves detail via Array.find()
 * against the list response instead.
 */

import { mcpUrl, mcpHeaders, extractError } from "./common"
import type { Brief, BriefKind } from "@/lib/types/brief"

/** GET /briefs?kind=&limit= — recent briefs of the given kind, newest first. */
export async function fetchBriefs(kind: BriefKind, limit = 20): Promise<Brief[]> {
  const res = await fetch(mcpUrl("/briefs", { kind, limit }), { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to load briefs"))
  return res.json() as Promise<Brief[]>
}
