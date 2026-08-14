// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { Badge } from "@/components/ui/badge"
import { useLiveDomains } from "@/hooks/use-live-domains"
import { domainSlot } from "@/lib/graph/identity"

interface DomainFilterProps {
  activeDomains: Set<string>
  onToggle: (domain: string) => void
}

export function DomainFilter({ activeDomains, onToggle }: DomainFilterProps) {
  // UX-08 — chips come from the live domain aggregate (connector domains
  // included), not a per-surface hardcoded list.
  const domains = useLiveDomains()
  return (
    <div className="flex min-w-0 flex-wrap gap-1.5">
      {domains.map((domain) => {
        const isActive = activeDomains.has(domain)
        const slot = domainSlot(domain)
        return (
          <Badge
            key={domain}
            variant={isActive ? "default" : "outline"}
            className="cursor-pointer text-xs capitalize"
            style={isActive ? undefined : {
              color: `var(--color-domain-${slot})`, // drift-allowed: runtime-derived token slot
              backgroundColor: `color-mix(in oklab, var(--color-domain-${slot}) 14%, transparent)`, // drift-allowed: runtime-derived token slot
            }}
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
