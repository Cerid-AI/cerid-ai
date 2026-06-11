// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Badge } from "@/components/ui/badge"
import { domainSlot } from "@/lib/graph/identity"

export function DomainBadge({ domain }: { domain: string }) {
  const slot = domainSlot(domain)
  return (
    <Badge
      variant="outline"
      className="text-xs capitalize"
      style={{
        color: `var(--color-domain-${slot})`, // drift-allowed: runtime-derived token slot; cannot be a static Tailwind class
        backgroundColor: `color-mix(in oklab, var(--color-domain-${slot}) 14%, transparent)`, // drift-allowed: runtime-derived token slot
      }}
    >
      {domain}
    </Badge>
  )
}
