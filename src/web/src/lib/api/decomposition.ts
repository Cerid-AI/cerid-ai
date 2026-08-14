// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Client for GET /graph/decomposition — full icicle payload and
// per-community entity leaf fetch.

import { mcpUrl, mcpHeaders, extractError } from "./common"
import type {
  DecompositionPayload,
  DecompositionCommunityPayload,
  DecompositionBucketPayload,
} from "@/lib/graph/cycle4-contracts"

export type {
  DecompositionPayload,
  DecompositionCommunityPayload,
  DecompositionBucketPayload,
}

export async function fetchDecomposition(
  options: { signal?: AbortSignal } = {},
): Promise<DecompositionPayload> {
  const url = mcpUrl("/graph/decomposition")
  const res = await fetch(url.toString(), {
    headers: mcpHeaders(),
    signal: options.signal,
  })
  if (!res.ok) throw new Error(await extractError(res, "Decomposition fetch failed"))
  return res.json() as Promise<DecompositionPayload>
}

export async function fetchCommunityEntities(
  communityId: string,
  options: { signal?: AbortSignal } = {},
): Promise<DecompositionCommunityPayload> {
  const url = mcpUrl("/graph/decomposition", { community: communityId })
  const res = await fetch(url.toString(), {
    headers: mcpHeaders(),
    signal: options.signal,
  })
  if (!res.ok) throw new Error(await extractError(res, "Community entity fetch failed"))
  return res.json() as Promise<DecompositionCommunityPayload>
}

export async function fetchBucketEntities(
  bucket: "unclustered" | "uncategorized",
  domain: string | null,
  options: { signal?: AbortSignal } = {},
): Promise<DecompositionBucketPayload> {
  const params: Record<string, string> = { bucket }
  if (domain) params.domain = domain
  const url = mcpUrl("/graph/decomposition", params)
  const res = await fetch(url.toString(), {
    headers: mcpHeaders(),
    signal: options.signal,
  })
  if (!res.ok) throw new Error(await extractError(res, "Bucket entity fetch failed"))
  return res.json() as Promise<DecompositionBucketPayload>
}
