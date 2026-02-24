import { Badge } from "@/components/ui/badge"
import { DOMAINS } from "@/lib/types"

const DOMAIN_COLORS: Record<string, string> = {
  coding: "bg-blue-500/10 text-blue-700 dark:text-blue-400 hover:bg-blue-500/20",
  finance: "bg-green-500/10 text-green-700 dark:text-green-400 hover:bg-green-500/20",
  projects: "bg-purple-500/10 text-purple-700 dark:text-purple-400 hover:bg-purple-500/20",
  personal: "bg-orange-500/10 text-orange-700 dark:text-orange-400 hover:bg-orange-500/20",
  general: "bg-zinc-500/10 text-zinc-700 dark:text-zinc-400 hover:bg-zinc-500/20",
}

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
            className={`cursor-pointer text-xs capitalize ${isActive ? "" : DOMAIN_COLORS[domain] ?? ""}`}
            onClick={() => onToggle(domain)}
          >
            {domain}
          </Badge>
        )
      })}
    </div>
  )
}

export function DomainBadge({ domain }: { domain: string }) {
  return (
    <Badge variant="outline" className={`text-xs capitalize ${DOMAIN_COLORS[domain] ?? ""}`}>
      {domain}
    </Badge>
  )
}
