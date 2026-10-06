// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Packaging guards for the published npm artifact. These assert the manifest
 * shape that resolvers depend on — the class of defect that only shows up in
 * a consumer's project, never in this repo's own build.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(readFileSync(path.join(__dirname, "..", "package.json"), "utf-8"));

describe("package.json exports map", () => {
  it("lists `types` before `import`", () => {
    // Export conditions match in declaration order: with `import` first, a
    // strict resolver never consults `types` and the package resolves with no
    // types at all. publint and arethetypeswrong both flag the other order.
    const conditions = Object.keys(pkg.exports["."]);
    expect(conditions.indexOf("types")).toBeLessThan(conditions.indexOf("import"));
  });

  it("declares the minimum Node it supports", () => {
    // The package relies on global fetch, so "any Node" is not true. Say which.
    expect(pkg.engines?.node).toBeTruthy();
  });

  it("resolves under require and default as well as import", () => {
    // A CommonJS consumer (cerid-finance's worker, via tsx) fails at import
    // with ERR_PACKAGE_PATH_NOT_EXPORTED when these conditions are absent.
    // Both point at the ESM bundle: tsx loads that file once the condition
    // exists. A separate CJS emit is not what that consumer checks.
    const root = pkg.exports["."];
    expect(root.require).toBe("./dist/index.js");
    expect(root.default).toBe("./dist/index.js");
    expect(root.import).toBe("./dist/index.js");
  });
});
