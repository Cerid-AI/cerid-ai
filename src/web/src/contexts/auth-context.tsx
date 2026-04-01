// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react"
import {
  authLogin,
  authLogout,
  authMe,
  authRefresh,
  authRegister,
} from "@/lib/api"
import type { AuthUser } from "@/lib/types"

interface AuthState {
  user: AuthUser | null
  isAuthenticated: boolean
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, displayName?: string, tenantName?: string) => Promise<void>
  logout: () => Promise<void>
  refreshToken: () => Promise<boolean>
}

const AuthContext = createContext<AuthState | null>(null)

const TOKEN_KEY = "cerid-access-token"
const REFRESH_KEY = "cerid-refresh-token"

function storeTokens(access: string, refresh: string) {
  try {
    localStorage.setItem(TOKEN_KEY, access)
    localStorage.setItem(REFRESH_KEY, refresh)
  } catch { /* noop */ }
}

function clearTokens() {
  try {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(REFRESH_KEY)
  } catch { /* noop */ }
}

function getRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_KEY)
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const initRef = useRef(false)

  // Try to restore session from stored token on mount
  useEffect(() => {
    if (initRef.current) return
    initRef.current = true

    const token = localStorage.getItem(TOKEN_KEY)
    if (!token) {
      setIsLoading(false)
      return
    }

    authMe()
      .then((user) => { setUser(user); setIsLoading(false) })
      .catch(() => {
        // Token may be expired — try refresh
        const rt = getRefreshToken()
        if (rt) {
          authRefresh(rt)
            .then((res) => {
              localStorage.setItem(TOKEN_KEY, res.access_token)
              return authMe()
            })
            .then(setUser)
            .catch(() => clearTokens())
            .finally(() => setIsLoading(false))
        } else {
          clearTokens()
          setIsLoading(false)
        }
      })
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const res = await authLogin(email, password)
    storeTokens(res.access_token, res.refresh_token)
    setUser(res.user)
  }, [])

  const register = useCallback(
    async (email: string, password: string, displayName?: string, tenantName?: string) => {
      const res = await authRegister(email, password, displayName, tenantName)
      storeTokens(res.access_token, res.refresh_token)
      setUser(res.user)
    },
    [],
  )

  const logout = useCallback(async () => {
    const rt = getRefreshToken()
    if (rt) await authLogout(rt).catch(() => { /* noop */ })
    clearTokens()
    setUser(null)
  }, [])

  const refreshTokenFn = useCallback(async (): Promise<boolean> => {
    const rt = getRefreshToken()
    if (!rt) return false
    try {
      const res = await authRefresh(rt)
      localStorage.setItem(TOKEN_KEY, res.access_token)
      return true
    } catch {
      clearTokens()
      setUser(null)
      return false
    }
  }, [])

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        isLoading,
        login,
        register,
        logout,
        refreshToken: refreshTokenFn,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used within AuthProvider")
  return ctx
}
