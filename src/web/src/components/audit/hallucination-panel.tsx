import { useState, useEffect } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { fetchHallucinationReport } from "@/lib/api"
import type { HallucinationReport, HallucinationClaim } from "@/lib/types"

const STATUS_COLORS: Record<string, string> = {
  verified: "bg-green-500/20 text-green-400 border-green-500/30",
  unverified: "bg-red-500/20 text-red-400 border-red-500/30",
  uncertain: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  error: "bg-muted text-muted-foreground",
}

function ClaimBadge({ claim }: { claim: HallucinationClaim }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border p-3">
      <Badge
        variant="outline"
        className={`shrink-0 ${STATUS_COLORS[claim.status] ?? STATUS_COLORS.error}`}
      >
        {claim.status}
      </Badge>
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-relaxed">{claim.claim}</p>
        {claim.source_filename && (
          <p className="mt-1 text-xs text-muted-foreground">
            Source: {claim.source_filename}
            {claim.similarity > 0 && ` (${Math.round(claim.similarity * 100)}% match)`}
          </p>
        )}
        {claim.reason && !claim.source_filename && (
          <p className="mt-1 text-xs text-muted-foreground">{claim.reason}</p>
        )}
      </div>
    </div>
  )
}

interface HallucinationPanelProps {
  conversationId: string | null
}

export function HallucinationPanel({ conversationId }: HallucinationPanelProps) {
  const [report, setReport] = useState<HallucinationReport | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!conversationId) {
      setReport(null)
      return
    }
    setLoading(true)
    fetchHallucinationReport(conversationId)
      .then(setReport)
      .catch(() => setReport(null))
      .finally(() => setLoading(false))
  }, [conversationId])

  if (!conversationId || loading) return null
  if (!report || report.skipped) return null
  if (report.summary.total === 0) return null

  const { verified, unverified, uncertain } = report.summary

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">Fact Check</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex gap-3 text-xs">
          {verified > 0 && (
            <span className="text-green-400">{verified} verified</span>
          )}
          {uncertain > 0 && (
            <span className="text-yellow-400">{uncertain} uncertain</span>
          )}
          {unverified > 0 && (
            <span className="text-red-400">{unverified} unverified</span>
          )}
        </div>
        <div className="space-y-2">
          {report.claims.map((claim, i) => (
            <ClaimBadge key={i} claim={claim} />
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
