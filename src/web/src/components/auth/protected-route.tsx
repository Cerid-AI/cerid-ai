// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { type ReactNode } from "react"
import { useAuth } from "@/contexts/auth-context"
import { LoginPage } from "./login-page"
import { Loader2 } from "lucide-react"

/**
 * Wraps children with auth check. Shows LoginPage when not authenticated.
 * Only active when multi-user mode is detected from server settings.
 */
export function ProtectedRoute({
  multiUser,
  children,
}: {
  multiUser: boolean
  children: ReactNode
}) {
  const { isAuthenticated, isLoading } = useAuth()

  // Single-user mode — no auth required
  if (!multiUser) return <>{children}</>

  // Loading auth state
  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  // Not authenticated — show login
  if (!isAuthenticated) return <LoginPage />

  return <>{children}</>
}
