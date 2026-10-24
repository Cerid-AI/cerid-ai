// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * The Settings encryption row names the variable the server reads (audit F31).
 *
 * It told operators to set CERID_ENCRYPTION, which nothing reads; field
 * encryption is on exactly when utils/encryption.py finds CERID_ENCRYPTION_KEY.
 */

import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

const ENCRYPTION_PY = path.resolve(__dirname, "..", "..", "..", "mcp", "utils", "encryption.py")
const PRIVACY_TS = path.resolve(__dirname, "..", "lib", "settings-registry", "privacy.ts")

function rowEnvVars(source: string, id: string): string[] {
  const start = source.indexOf(`id: "${id}"`)
  expect(start, `${id} not found in privacy.ts`).toBeGreaterThan(-1)
  const end = source.indexOf("\n  },", start)
  const row = source.slice(start, end)
  return [...row.matchAll(/envVar: "([A-Z0-9_]+)"/g)].map((m) => m[1])
}

describe("encryption setting env contract", () => {
  it("the registry row's env var is the one utils/encryption.py reads", () => {
    const server = readFileSync(ENCRYPTION_PY, "utf8")
    const vars = rowEnvVars(readFileSync(PRIVACY_TS, "utf8"), "privacy.data.encryption")
    expect(vars.length).toBe(1)
    expect(server.includes(`os.getenv("${vars[0]}"`)).toBe(true)
  })
})
