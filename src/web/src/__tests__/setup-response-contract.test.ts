// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Drift gate over the /setup response shapes.
 *
 * A TS response interface that declares a field the endpoint never sends is
 * invisible: the read compiles, returns undefined, and whatever it gated on
 * silently never happens. Both /setup/status and /setup/validate-key had
 * accumulated such fields. This pins the two interfaces in lib/types.ts to
 * the pydantic models that are the routes' response_model.
 */

import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

const SETUP_PY = path.resolve(__dirname, "..", "..", "..", "mcp", "app", "routers", "setup.py")
const TYPES_TS = path.resolve(__dirname, "..", "lib", "types.ts")

/** Field names of a pydantic model, in declaration order. */
function pydanticFields(source: string, model: string): string[] {
  const start = source.indexOf(`class ${model}(BaseModel):`)
  expect(start, `${model} not found in setup.py`).toBeGreaterThan(-1)
  const body = source.slice(start).split("\n").slice(1)
  const fields: string[] = []
  for (const line of body) {
    if (line.trim() === "" || line.trimStart().startsWith("#")) continue
    if (!line.startsWith("    ")) break
    const m = /^ {4}(\w+)\s*:/.exec(line)
    if (m) fields.push(m[1])
  }
  return fields
}

/** Field names of a TS interface, `?` stripped. */
function interfaceFields(source: string, name: string): string[] {
  const start = source.indexOf(`export interface ${name} {`)
  expect(start, `${name} not found in types.ts`).toBeGreaterThan(-1)
  const body = source.slice(start).split("\n").slice(1)
  const fields: string[] = []
  for (const line of body) {
    if (line.startsWith("}")) break
    const m = /^ {2}(\w+)\??\s*:/.exec(line)
    if (m) fields.push(m[1])
  }
  return fields
}

const setupPy = readFileSync(SETUP_PY, "utf8")
const typesTs = readFileSync(TYPES_TS, "utf8")

describe("/setup response contracts", () => {
  it("SetupStatus declares exactly the fields GET /setup/status returns", () => {
    expect(new Set(interfaceFields(typesTs, "SetupStatus"))).toEqual(
      new Set(pydanticFields(setupPy, "SetupStatus")),
    )
  })

  it("KeyValidation declares exactly the fields POST /setup/validate-key returns", () => {
    expect(new Set(interfaceFields(typesTs, "KeyValidation"))).toEqual(
      new Set(pydanticFields(setupPy, "KeyValidationResponse")),
    )
  })
})
