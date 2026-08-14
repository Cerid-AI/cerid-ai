// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useDeepLinks } from "@/hooks/use-deep-links"

/**
 * Renders nothing; exists so `useDeepLinks` runs inside NavigationProvider,
 * which it needs for `goTo`. Mount exactly once — see the hook.
 */
export function DeepLinkRouter() {
  useDeepLinks()
  return null
}
