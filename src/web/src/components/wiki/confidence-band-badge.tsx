// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Deprecated: use TrustBandBadge from @/components/ui/trust-band-badge instead.
// This file is kept as a thin wrapper so existing tests and barrel exports
// continue to resolve without changes during the Atlas v2 Gazetteer landing.

import type { ConfidenceBand } from "@/lib/types/wiki"
import { TrustBandBadge, type TrustState } from "@/components/ui/trust-band-badge"

interface ConfidenceBandBadgeProps {
  band: ConfidenceBand
  className?: string
}

function confidenceBandToTrust(band: ConfidenceBand): TrustState {
  switch (band) {
    case "high": return "verified"
    case "medium": return "partial"
    case "low": return "unverified"
    default: return "unknown"
  }
}

export function ConfidenceBandBadge({ band, className }: ConfidenceBandBadgeProps) {
  return (
    <TrustBandBadge
      trust={confidenceBandToTrust(band)}
      className={className}
    />
  )
}
