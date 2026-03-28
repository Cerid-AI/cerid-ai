// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Badge } from "@/components/ui/badge"
import { DOMAIN_BADGE_COLORS } from "@/lib/constants"

export function DomainBadge({ domain }: { domain: string }) {
  return (
    <Badge variant="outline" className={`text-xs capitalize ${DOMAIN_BADGE_COLORS[domain] ?? "bg-zinc-500/20 text-zinc-400"}`}>
      {domain}
    </Badge>
  )
}
