// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export function matchesSearch(name: string, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (q === "") return true
  return name.toLowerCase().includes(q)
}

export function isOrphan(degree: number): boolean {
  return degree === 0
}
