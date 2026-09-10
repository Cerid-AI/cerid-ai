// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Pins the global `sigma` stub installed by setup.ts. Without it, any test
// that reaches a real Atlas import evaluates sigma's CJS build, which touches
// WebGLRenderingContext at module scope and is undefined under jsdom — an
// unhandled error that fails the run from outside any test body.

import { describe, it, expect } from "vitest"

// The real "sigma" package's types (constructor: graph, container, settings?)
// don't match the zero-arg stub installed by setup.ts, so the import is cast
// to the stub's own shape rather than the real declaration.
type SigmaStubCtor = new () => { on: (...args: unknown[]) => unknown }

describe("sigma under jsdom", () => {
  it("resolves to a stub that constructs without a WebGL context", async () => {
    const mod = await import("sigma")
    const Sigma = mod.default as unknown as SigmaStubCtor
    expect(typeof Sigma).toBe("function")
    const instance = new Sigma()
    expect(instance).toBeDefined()
    expect(instance.on()).toBe(instance)
  })
})
