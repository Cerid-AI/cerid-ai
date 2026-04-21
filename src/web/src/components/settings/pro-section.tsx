// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Badge } from "@/components/ui/badge"
import { Crown } from "lucide-react"

interface ProSectionProps {
  featureTier: string
  featureFlags: Record<string, boolean>
  onRefresh?: () => void
}

export function ProSection({ featureTier }: ProSectionProps) {
  const isPro = featureTier === "pro" || featureTier === "enterprise"

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Crown className="h-5 w-5 text-amber-500" />
        <h3 className="font-semibold">Pro Features</h3>
        <Badge variant={isPro ? "default" : "secondary"}>
          {isPro ? "Active" : "Community"}
        </Badge>
      </div>

      {isPro ? (
        <p className="text-sm text-muted-foreground">
          Pro tier is active. All advanced features are enabled.
        </p>
      ) : (
        <div className="rounded-lg border border-border p-4 text-sm text-muted-foreground space-y-2">
          <p>
            Pro tier unlocks advanced features: intelligent context assembly,
            custom RAG source weights, workflow engine, and priority support.
          </p>
          <p>
            Visit <a href="https://cerid.ai/pro" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">cerid.ai/pro</a> for details.
          </p>
        </div>
      )}
    </div>
  )
}
