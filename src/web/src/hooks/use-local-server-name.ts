// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useQuery } from "@tanstack/react-query"
import { fetchHealthStatus } from "@/lib/api"

/**
 * What the local model server calls itself, from `/health/status`. Shares the
 * status bar's query, so it costs no request of its own. Undefined until
 * health has answered, or on an instance with no local provider.
 */
export function useLocalServerName(): string | undefined {
  // eslint-disable-next-line cerid/no-query-error-as-empty -- deliberate: with health unreachable the name is unknown and callers show their neutral label
  const { data } = useQuery({
    queryKey: ["health-status"],
    queryFn: fetchHealthStatus,
    refetchInterval: 15_000,
    retry: 1,
  })
  return data?.local_model_server?.name
}
