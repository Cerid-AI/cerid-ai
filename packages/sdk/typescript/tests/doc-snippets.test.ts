// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * The published TypeScript snippets must compile and run.
 *
 * README.md is the npm landing page and docs/SDK_GUIDE.md is the canonical
 * integration guide; both shipped snippets that called methods the client
 * does not have. Two layers close that: doc-snippets.ts is compiled by
 * `npm run typecheck`, and the assertions below prove the markdown still
 * matches it and that the calls survive a round trip.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

import { CeridClient } from "../src/index.js";
import { guideKeepWebOut, guideQuickstart, readmeUsage } from "./doc-snippets.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PACKAGE_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(PACKAGE_ROOT, "../../..");

const COMPILED = readFileSync(path.join(__dirname, "doc-snippets.ts"), "utf-8");

/** Lines that carry API surface — imports, construction and blank lines are
 * asserted elsewhere or carry no call. */
function meaningfulLines(block: string): string[] {
  return block
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .filter((line) => !line.startsWith("import "))
    .filter((line) => !line.startsWith("//"))
    .filter((line) => !line.includes("new CeridClient"))
    .filter((line) => !/^(baseUrl|clientId|apiKey)\s*:/.test(line))
    .filter((line) => !/^\}\)?[;,]?$/.test(line));
}

function tsBlocks(file: string): string[] {
  const text = readFileSync(file, "utf-8");
  return [...text.matchAll(/```(?:ts|typescript)\n([\s\S]*?)```/g)].map((m) => m[1]);
}

const DOCS: Array<[string, string]> = [
  ["README.md", path.join(PACKAGE_ROOT, "README.md")],
  ["docs/SDK_GUIDE.md", path.join(REPO_ROOT, "docs", "SDK_GUIDE.md")],
];

describe("published TypeScript snippets are the compiled ones", () => {
  for (const [label, file] of DOCS) {
    it(`${label} snippets appear verbatim in doc-snippets.ts`, () => {
      const blocks = tsBlocks(file);
      expect(blocks.length, `${label}: no TypeScript snippet found`).toBeGreaterThan(0);
      for (const line of blocks.flatMap(meaningfulLines)) {
        expect(COMPILED, `${label} publishes a line that is not compiled: ${line}`).toContain(line);
      }
    });
  }
});

describe("published TypeScript snippets run", () => {
  function mockedClient() {
    const bodies: Record<string, unknown> = {
      "/sdk/v1/query": {
        context: "c", sources: [], confidence: 0.9, domains_searched: ["coding"],
        total_results: 1, token_budget_used: 10, graph_results: 0,
        results: [{ content: "chunk", relevance: 0.9 }],
      },
      "/sdk/v1/hallucination": {
        conversation_id: "demo", timestamp: "", skipped: false, reason: null, claims: [],
        summary: { total: 1, verified: 1, overall_confidence: 0.9 }, mode: "thorough", nli_skipped: false,
      },
      "/sdk/v1/memory/extract": {
        conversation_id: "demo", timestamp: "", memories_extracted: 1, memories_stored: 1,
        skipped_duplicates: 0, results: [],
      },
      "/sdk/v1/ingest": { status: "success", artifact_id: "art-1", chunks: 1, domain: "databases" },
      "/sdk/v1/health": { status: "healthy", version: "1.1.0", services: {}, features: {} },
    };
    const fetchMock = vi.fn(async (url: string) => {
      const key = Object.keys(bodies).find((k) => String(url).endsWith(k));
      return new Response(JSON.stringify(bodies[key as string]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    return new CeridClient({
      baseUrl: "http://localhost:8888",
      clientId: "my-app",
      fetch: fetchMock as unknown as typeof globalThis.fetch,
    });
  }

  it("README usage round-trips", async () => {
    await expect(readmeUsage(mockedClient())).resolves.toBeUndefined();
  });

  it("SDK_GUIDE quickstart round-trips", async () => {
    await expect(guideQuickstart(mockedClient())).resolves.toBeUndefined();
  });

  it("SDK_GUIDE web-exclusion example round-trips", async () => {
    await expect(guideKeepWebOut(mockedClient())).resolves.toBeUndefined();
  });
});
