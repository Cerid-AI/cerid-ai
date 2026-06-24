// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { mcpUrl, mcpHeaders, extractError } from "./common"

export interface UpdateCheckResult {
  running: string
  latest: string | null
  update_available: boolean
  release_url: string | null
  error?: string | null
}

export async function checkForUpdates(force = false): Promise<UpdateCheckResult> {
  const url = mcpUrl(force ? "/updates/check?force=true" : "/updates/check")
  const res = await fetch(url, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Update check failed: ${res.status}`))
  return res.json() as Promise<UpdateCheckResult>
}
