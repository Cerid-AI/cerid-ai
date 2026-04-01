// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Badge } from "@/components/ui/badge"
import { DOMAINS } from "@/lib/types"
import { DOMAIN_BADGE_COLORS } from "@/lib/constants"

interface DomainFilterProps {
  activeDomains: Set<string>
  onToggle: (domain: string) => void
}

export function DomainFilter({ activeDomains, onToggle }: DomainFilterProps) {
  return (
    <div className="flex min-w-0 flex-wrap gap-1.5">
      {DOMAINS.map((domain) => {
        const isActive = activeDomains.has(domain)
        return (
          <Badge
            key={domain}
            variant={isActive ? "default" : "outline"}
            className={`cursor-pointer text-xs capitalize ${isActive ? "" : DOMAIN_BADGE_COLORS[domain] ?? ""}`}
            role="button"
            tabIndex={0}
            aria-pressed={isActive}
            onClick={() => onToggle(domain)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(domain) } }}
          >
            {domain}
          </Badge>
        )
      })}
    </div>
  )
}

// DomainBadge has been promoted to @/components/ui/domain-badge
export { DomainBadge } from "@/components/ui/domain-badge"