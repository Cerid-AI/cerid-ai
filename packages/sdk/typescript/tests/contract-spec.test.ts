// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Contract tests: pin the TypeScript SDK against docs/openapi-sdk-v1.json.
 *
 * docs/openapi-sdk-v1.json is generated from the live FastAPI routes by
 * scripts/gen_sdk_openapi.py and is the authoritative `/sdk/v1/*` contract
 * (the `sdk-openapi-drift` CI gate keeps it byte-for-byte in sync with the
 * server). This is the TypeScript half of the pin — the Python half lives
 * at packages/sdk/python/tests/test_contract_spec.py and uses `jsonschema`
 * against the same file.
 *
 * TypeScript interfaces have no runtime representation, so a shape check
 * needs two complementary layers here:
 *
 *   1. Compile-time — every `invoke` below constructs its request as an
 *      object literal typed against the real exported request interface
 *      (e.g. `MemoryExtractRequest`). If a field is renamed in `types.ts`
 *      so it no longer matches how a correctly-typed caller would write
 *      it, `npm run typecheck` (added alongside this file — see
 *      package.json) fails with a missing/excess-property error. That
 *      catches drift in the *declared* shape.
 *   2. Runtime — this file's `assertRequestMatchesSchema` re-validates the
 *      JSON body the client actually put on the wire (captured via a
 *      mocked `fetch`) against the spec's `requestBody` schema. That
 *      catches drift in what's *actually sent*, independent of what the
 *      type declares — the class of bug the finance client shipped
 *      (calling `llm/complete` with `prompt` instead of `messages`) is a
 *      runtime body-shape bug, not a type error, and only layer 2 catches
 *      it directly.
 *
 * No new dependency: this hand-rolls the ~30 lines of JSON-Schema-lite
 * checking it needs (required-property presence + primitive type match)
 * rather than pulling in ajv for a repo that doesn't otherwise use it.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

import { CeridClient } from "../src/index.js";

// ---------------------------------------------------------------------------
// Spec loading + a minimal JSON-Schema-lite checker (no new dependency).
// ---------------------------------------------------------------------------

type JSONSchema = Record<string, any>;

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../../../../");
const SPEC: JSONSchema = JSON.parse(
  readFileSync(path.join(REPO_ROOT, "docs", "openapi-sdk-v1.json"), "utf-8"),
);

function resolveRef(schema: JSONSchema): JSONSchema {
  if (schema.$ref) {
    const name = String(schema.$ref).split("/").pop() as string;
    return SPEC.components.schemas[name];
  }
  return schema;
}

function requestSchema(p: string, method: string): JSONSchema {
  return resolveRef(SPEC.paths[p][method].requestBody.content["application/json"].schema);
}

function responseSchema(p: string, method: string, status = "200"): JSONSchema {
  return resolveRef(SPEC.paths[p][method].responses[status].content["application/json"].schema);
}

function typeOf(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value;
}

/** Does `value` satisfy `schema`'s `type`/`anyOf` constraint? Untyped => unconstrained (JSON Schema semantics). */
function schemaAccepts(schemaIn: JSONSchema, value: unknown): boolean {
  const schema = resolveRef(schemaIn);
  if (schema.anyOf) {
    return (schema.anyOf as JSONSchema[]).some((s) => schemaAccepts(s, value));
  }
  if (!schema.type) return true;
  const actual = typeOf(value);
  if (schema.type === "integer") return actual === "number" && Number.isInteger(value as number);
  return actual === schema.type;
}

/** Asserts `body` (a real captured request payload) satisfies `schema`'s required properties and known-property types. */
function assertRequestMatchesSchema(body: Record<string, unknown>, schema: JSONSchema): void {
  const resolved = resolveRef(schema);
  const required: string[] = resolved.required ?? [];
  for (const key of required) {
    expect(body, `missing required property "${key}"`).toHaveProperty(key);
  }
  const properties: Record<string, JSONSchema> = resolved.properties ?? {};
  for (const [key, value] of Object.entries(body)) {
    const propSchema = properties[key];
    if (!propSchema) continue; // unknown key: spec doesn't constrain it (additionalProperties not set to false anywhere here)
    expect(
      schemaAccepts(propSchema, value),
      `property "${key}" = ${JSON.stringify(value)} doesn't match its spec type`,
    ).toBe(true);
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// ---------------------------------------------------------------------------
// Endpoint table: one row per wrapped `/sdk/v1/*` POST method.
// ---------------------------------------------------------------------------

interface PostCase {
  label: string;
  path: string;
  method: "post";
  responseFixture: Record<string, unknown>;
  invoke: (client: CeridClient) => Promise<unknown>;
}

const POST_CASES: PostCase[] = [
  {
    label: "kb.query",
    path: "/sdk/v1/query",
    method: "post",
    responseFixture: {
      context: "c", sources: [], confidence: 0.5, domains_searched: [], total_results: 0,
      token_budget_used: 0, graph_results: 0, results: [],
    },
    invoke: (c) => c.kb.query({ query: "test query", domains: ["general"], top_k: 5 }),
  },
  {
    label: "kb.search",
    path: "/sdk/v1/search",
    method: "post",
    responseFixture: { results: [], total_results: 0, confidence: 0 },
    invoke: (c) => c.kb.search({ query: "test query", domain: "general", top_k: 3 }),
  },
  {
    label: "kb.ingestExternal",
    path: "/sdk/v1/ingest/external",
    method: "post",
    responseFixture: { accepted: 1, skipped: 0, errors: [], source_type: "readwise" },
    invoke: (c) =>
      c.kb.ingestExternal({
        source_type: "readwise",
        payload: { highlights: [{ text: "h1", url: "https://example.com/h1" }] },
        field_mappings: { content: "highlights[].text", source_uri: "highlights[].url" },
      }),
  },
  {
    label: "verify.check",
    path: "/sdk/v1/hallucination",
    method: "post",
    responseFixture: { conversation_id: "conv-1", timestamp: "", skipped: false, reason: null, claims: [], summary: {} },
    invoke: (c) => c.verify.check({ response_text: "The sky is blue.", conversation_id: "conv-1" }),
  },
  {
    label: "memory.extract",
    path: "/sdk/v1/memory/extract",
    method: "post",
    responseFixture: {
      conversation_id: "conv-1", timestamp: "", memories_extracted: 1, memories_stored: 1,
      skipped_duplicates: 0, results: [],
    },
    invoke: (c) => c.memory.extract({ response_text: "I prefer dark mode.", conversation_id: "conv-1" }),
  },
  {
    label: "llm.complete",
    path: "/sdk/v1/llm/complete",
    method: "post",
    responseFixture: {
      content: "Yes.", model: "openai/gpt-4o-mini", provider: "openrouter_paid",
      reason: "", estimated_cost_per_1k: 0, tier_p95_ms: 0,
    },
    invoke: (c) => c.llm.complete({ messages: [{ role: "user", content: "Hi" }], task_type: "internal" }),
  },
];

describe("POST request bodies match the spec", () => {
  for (const tc of POST_CASES) {
    it(`${tc.label} sends a spec-conformant request body`, async () => {
      const mockFetch = vi.fn().mockResolvedValue(jsonResponse(tc.responseFixture));
      const client = new CeridClient({ baseUrl: "http://localhost:8888", clientId: "test", fetch: mockFetch });

      await tc.invoke(client);

      const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
      const body = JSON.parse(init.body as string);
      assertRequestMatchesSchema(body, requestSchema(tc.path, tc.method));
    });
  }
});

describe("Response fixtures satisfy the spec's response schema", () => {
  for (const tc of POST_CASES) {
    it(`${tc.label}'s fixture validates against the 200 schema`, () => {
      const schema = responseSchema(tc.path, tc.method);
      const required: string[] = schema.required ?? [];
      for (const key of required) {
        expect(tc.responseFixture, `${tc.label}: fixture missing spec-required "${key}"`).toHaveProperty(key);
      }
    });
  }
});

// ---------------------------------------------------------------------------
// GET endpoints: no request body — response-shape only.
// ---------------------------------------------------------------------------

interface GetCase {
  label: string;
  path: string;
  method: "get";
  responseFixture: Record<string, unknown>;
  invoke: (client: CeridClient) => Promise<unknown>;
}

const GET_CASES: GetCase[] = [
  {
    label: "system.health",
    path: "/sdk/v1/health",
    method: "get",
    responseFixture: { status: "healthy", version: "1.1.0", services: {}, features: {} },
    invoke: (c) => c.system.health(),
  },
  {
    label: "system.settings",
    path: "/sdk/v1/settings",
    method: "get",
    responseFixture: { version: "1.1.0", tier: "community", features: {} },
    invoke: (c) => c.system.settings(),
  },
  {
    label: "system.plugins",
    path: "/sdk/v1/plugins",
    method: "get",
    responseFixture: { plugins: [], total: 0 },
    invoke: (c) => c.system.plugins(),
  },
  {
    label: "kb.taxonomy",
    path: "/sdk/v1/taxonomy",
    method: "get",
    responseFixture: { domains: ["general"], taxonomy: {} },
    invoke: (c) => c.kb.taxonomy(),
  },
  {
    label: "memory.getJob",
    path: "/sdk/v1/memory/extract/jobs/{job_id}",
    method: "get",
    responseFixture: { job_id: "job-123", status: "finished" },
    invoke: (c) => c.memory.getJob("job-123"),
  },
];

describe("GET endpoints round-trip through the spec's response schema", () => {
  for (const tc of GET_CASES) {
    it(`${tc.label} fixture satisfies the required response fields`, async () => {
      const schema = responseSchema(tc.path, tc.method);
      const required: string[] = schema.required ?? [];
      for (const key of required) {
        expect(tc.responseFixture, `${tc.label}: fixture missing spec-required "${key}"`).toHaveProperty(key);
      }

      const mockFetch = vi.fn().mockResolvedValue(jsonResponse(tc.responseFixture));
      const client = new CeridClient({ baseUrl: "http://localhost:8888", clientId: "test", fetch: mockFetch });
      const result = await tc.invoke(client);
      for (const key of required) {
        expect(result, `${tc.label}: result missing "${key}"`).toHaveProperty(key);
      }
    });
  }
});

describe("Spec version pin", () => {
  it("docs/openapi-sdk-v1.json declares a version", () => {
    // The TS SDK doesn't carry its own protocol-version constant (unlike
    // the Python SDK's SDK_PROTOCOL_VERSION) — this just guards against the
    // spec losing its version field entirely. See CONTRIBUTING.md "SDK
    // contract & versioning" for the bump discipline.
    expect(typeof SPEC.info.version).toBe("string");
    expect(SPEC.info.version.length).toBeGreaterThan(0);
  });
});
