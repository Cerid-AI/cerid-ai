// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ReactNode } from "react"
import { useUIMode } from "@/contexts/ui-mode-context"

interface AdvancedModeProps {
  /** Content shown only in advanced mode. */
  children: ReactNode
  /** Fallback rendered in simple mode. Defaults to null. */
  fallback?: ReactNode
}

/**
 * Conditional wrapper that renders `children` only when UI mode is "advanced".
 * In simple mode, renders `fallback` (default: nothing).
 *
 * Usage:
 *   <AdvancedMode>
 *     <PowerUserPanel />
 *   </AdvancedMode>
 *
 * or with a fallback hint:
 *   <AdvancedMode fallback={<p>Enable Advanced mode to see this.</p>}>
 *     <DeepSettings />
 *   </AdvancedMode>
 */
export function AdvancedMode({ children, fallback = null }: AdvancedModeProps) {
  const { isSimple } = useUIMode()
  return isSimple ? <>{fallback}</> : <>{children}</>
}
