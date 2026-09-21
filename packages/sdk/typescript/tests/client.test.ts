// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  CeridClient,
  SDK_PROTOCOL_VERSION,
  ProtocolVersionError,
  isMemoryExtractAccepted,
  CeridSDKError,
  AuthenticationError,
  DomainRestrictedError,
  RateLimitError,
  ValidationError,
  NotFoundError,
  ServiceUnavailableError,
} from "../src/index.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function createClient(fetchMock: typeof globalThis.fetch) {
  return new CeridClient({
    baseUrl: "http://localhost:8888",
    clientId: "test-client",
    apiKey: "test-key", // pragma: allowlist secret
    fetch: fetchMock,
  });
}

// ---------------------------------------------------------------------------
// Client construction
// ---------------------------------------------------------------------------

describe("CeridClient construction", () => {
  it("creates resource groups", () => {
    const client = new CeridClient({
      baseUrl: "http://localhost:8888",
      clientId: "test",
    });
    expect(client.kb).toBeDefined();
    expect(client.verify).toBeDefined();
    expect(client.memory).toBeDefined();
    expect(client.system).toBeDefined();
    expect(client.llm).toBeDefined();
  });

  it("strips trailing slashes from baseUrl", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ status: "healthy", version: "1.1.0", services: {}, features: {} }),
    );
    const client = new CeridClient({
      baseUrl: "http://localhost:8888///",
      clientId: "test",
      fetch: mockFetch,
    });
    await client.system.health();
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8888/sdk/v1/health",
      expect.any(Object),
    );
  });
});

// ---------------------------------------------------------------------------
// Header injection
// ---------------------------------------------------------------------------

describe("Header injection", () => {
  it("sends X-Client-ID on GET requests", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ status: "healthy", version: "1.1.0", services: {}, features: {} }),
    );
    const client = createClient(mockFetch);
    await client.system.health();

    const [, init] = mockFetch.mock.calls[0];
    expect(init.headers["X-Client-ID"]).toBe("test-client");
    expect(init.headers["X-API-Key"]).toBe("test-key");
  });

  it("sends Content-Type on POST requests", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ context: "", sources: [], confidence: 0, domains_searched: [], total_results: 0, token_budget_used: 0, graph_results: 0, results: [] }),
    );
    const client = createClient(mockFetch);
    await client.kb.query({ query: "test" });

    const [, init] = mockFetch.mock.calls[0];
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.method).toBe("POST");
  });

  it("omits X-API-Key when apiKey is not provided", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ status: "healthy", version: "1.1.0", services: {}, features: {} }),
    );
    const client = new CeridClient({
      baseUrl: "http://localhost:8888",
      clientId: "test",
      fetch: mockFetch,
    });
    await client.system.health();

    const [, init] = mockFetch.mock.calls[0];
    expect(init.headers["X-API-Key"]).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// KB endpoints
// ---------------------------------------------------------------------------

describe("KB resource", () => {
  let mockFetch: ReturnType<typeof vi.fn<typeof fetch>>;
  let client: CeridClient;

  beforeEach(() => {
    mockFetch = vi.fn<typeof fetch>();
    client = createClient(mockFetch);
  });

  it("query() POSTs to /sdk/v1/query", async () => {
    const body = { context: "result", sources: [], confidence: 0.9, domains_searched: ["general"], total_results: 1, token_budget_used: 100, graph_results: 0, results: [] };
    mockFetch.mockResolvedValue(jsonResponse(body));

    const result = await client.kb.query({ query: "What is RAG?", top_k: 5 });
    expect(result.context).toBe("result");
    expect(result.confidence).toBe(0.9);

    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8888/sdk/v1/query");
    expect(JSON.parse(init.body as string)).toEqual({ query: "What is RAG?", top_k: 5 });
  });

  it("search() POSTs to /sdk/v1/search", async () => {
    const body = { results: [{ chunk: "test" }], total_results: 1, confidence: 0.8 };
    mockFetch.mockResolvedValue(jsonResponse(body));

    const result = await client.kb.search({ query: "test", domain: "code", top_k: 3 });
    expect(result.total_results).toBe(1);

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe("http://localhost:8888/sdk/v1/search");
  });

  it("ingest() POSTs to /sdk/v1/ingest", async () => {
    const body = { status: "ok", artifact_id: "abc-123", chunks: 4, domain: "general" };
    mockFetch.mockResolvedValue(jsonResponse(body));

    const result = await client.kb.ingest({ content: "Some text", domain: "code", tags: "test" });
    expect(result.status).toBe("ok");
    expect(result.chunks).toBe(4);
  });

  it("ingestFile() POSTs to /sdk/v1/ingest/file", async () => {
    const body = { status: "ok", artifact_id: "def-456", chunks: 12, domain: "finance" };
    mockFetch.mockResolvedValue(jsonResponse(body));

    const result = await client.kb.ingestFile({ file_path: "/tmp/doc.pdf" });
    expect(result.artifact_id).toBe("def-456");

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe("http://localhost:8888/sdk/v1/ingest/file");
  });

  it("collections() GETs /sdk/v1/collections", async () => {
    const body = { collections: ["general", "code"], total: 2 };
    mockFetch.mockResolvedValue(jsonResponse(body));

    const result = await client.kb.collections();
    expect(result.collections).toEqual(["general", "code"]);
    expect(result.total).toBe(2);
  });

  it("taxonomy() GETs /sdk/v1/taxonomy", async () => {
    const body = { domains: ["general"], taxonomy: { general: {} } };
    mockFetch.mockResolvedValue(jsonResponse(body));

    const result = await client.kb.taxonomy();
    expect(result.domains).toEqual(["general"]);
  });
});

// ---------------------------------------------------------------------------
// Verify resource
// ---------------------------------------------------------------------------

describe("Verify resource", () => {
  it("check() POSTs to /sdk/v1/hallucination", async () => {
    const body = {
      conversation_id: "conv-1",
      timestamp: "2026-04-01T00:00:00Z",
      skipped: false,
      reason: null,
      claims: [{ text: "claim1", status: "verified" }],
      summary: { total: 1, verified: 1, unverified: 0, uncertain: 0 },
    };
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(body));
    const client = createClient(mockFetch);

    const result = await client.verify.check({
      response_text: "The sky is blue.",
      conversation_id: "conv-1",
    });
    expect(result.claims).toHaveLength(1);
    expect(result.summary.verified).toBe(1);

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe("http://localhost:8888/sdk/v1/hallucination");
  });
});

// ---------------------------------------------------------------------------
// Memory resource
// ---------------------------------------------------------------------------

describe("Memory resource", () => {
  it("extract() POSTs to /sdk/v1/memory/extract", async () => {
    const body = {
      conversation_id: "conv-2",
      timestamp: "2026-04-01T00:00:00Z",
      memories_extracted: 3,
      memories_stored: 2,
      skipped_duplicates: 1,
      results: [],
    };
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(body));
    const client = createClient(mockFetch);

    const result = await client.memory.extract({
      response_text: "I prefer dark mode.",
      conversation_id: "conv-2",
    });
    expect(result.memories_extracted).toBe(3);
    expect(result.skipped_duplicates).toBe(1);
  });

  it("getJob() GETs the job-status endpoint and url-encodes the id", async () => {
    const body = {
      job_id: "abc/1",
      status: "finished",
      enqueued_at: "2026-04-01T00:00:00Z",
      started_at: "2026-04-01T00:00:01Z",
      ended_at: "2026-04-01T00:00:02Z",
      result: {
        conversation_id: "conv-2",
        timestamp: "2026-04-01T00:00:02Z",
        memories_extracted: 1,
        memories_stored: 1,
        skipped_duplicates: 0,
        results: [],
      },
      error: null,
    };
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(body));
    const client = createClient(mockFetch);

    const status = await client.memory.getJob("abc/1");
    expect(status.status).toBe("finished");
    expect(status.result?.memories_stored).toBe(1);
    // Verify URL encoding applied to the path segment
    const url = (mockFetch.mock.calls[0] as [string, RequestInit])[0];
    expect(url).toContain("/sdk/v1/memory/extract/jobs/abc%2F1");
  });
});

// ---------------------------------------------------------------------------
// LLM resource
// ---------------------------------------------------------------------------

describe("LLM resource", () => {
  it("complete() POSTs to /sdk/v1/llm/complete with messages", async () => {
    const body = {
      content: "Yes.",
      model: "openai/gpt-4o-mini",
      provider: "openrouter_paid",
      reason: "task_type=internal",
      estimated_cost_per_1k: 0.00015,
      tier_p95_ms: 1200,
    };
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(body));
    const client = createClient(mockFetch);

    const result = await client.llm.complete({
      messages: [{ role: "user", content: "Is the sky blue?" }],
      task_type: "internal",
      slo_budget_ms: 5000,
    });
    expect(result.content).toBe("Yes.");
    expect(result.tier_p95_ms).toBe(1200);

    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8888/sdk/v1/llm/complete");
    expect(JSON.parse(init.body as string)).toMatchObject({
      messages: [{ role: "user", content: "Is the sky blue?" }],
      slo_budget_ms: 5000,
    });
  });
});

// ---------------------------------------------------------------------------
// External ingest
// ---------------------------------------------------------------------------

describe("KB ingestExternal", () => {
  it("POSTs adapter-shaped payloads to /sdk/v1/ingest/external", async () => {
    const body = { accepted: 2, skipped: 0, errors: [], source_type: "readwise" };
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(body));
    const client = createClient(mockFetch);

    const result = await client.kb.ingestExternal({
      source_type: "readwise",
      payload: { highlights: [{ text: "h1" }, { text: "h2" }] },
      field_mappings: { content: "highlights[].text" },
    });
    expect(result.accepted).toBe(2);
    expect(result.source_type).toBe("readwise");

    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8888/sdk/v1/ingest/external");
  });
});

// ---------------------------------------------------------------------------
// System resource
// ---------------------------------------------------------------------------

describe("System resource", () => {
  let mockFetch: ReturnType<typeof vi.fn<typeof fetch>>;
  let client: CeridClient;

  beforeEach(() => {
    mockFetch = vi.fn<typeof fetch>();
    client = createClient(mockFetch);
  });

  it("health() GETs /sdk/v1/health", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ status: "healthy", version: "1.1.0", services: { chromadb: "ok" }, features: {} }));
    const result = await client.system.health();
    expect(result.status).toBe("healthy");
  });

  it("healthDetailed() GETs /sdk/v1/health/detailed", async () => {
    mockFetch.mockResolvedValue(jsonResponse({
      status: "healthy", version: "1.1.0", services: {},
      features: {}, circuit_breakers: { neo4j: "closed" },
      degradation_tier: "FULL", uptime_seconds: 3600,
    }));
    const result = await client.system.healthDetailed();
    expect(result.degradation_tier).toBe("FULL");
    expect(result.uptime_seconds).toBe(3600);
  });

  it("settings() GETs /sdk/v1/settings", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ version: "1.1.0", tier: "community", features: { enable_self_rag: true } }));
    const result = await client.system.settings();
    expect(result.tier).toBe("community");
  });

  it("plugins() GETs /sdk/v1/plugins", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ plugins: [{ name: "audio", status: "active" }], total: 1 }));
    const result = await client.system.plugins();
    expect(result.plugins).toHaveLength(1);
    expect(result.total).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// Error mapping
// ---------------------------------------------------------------------------

describe("Error mapping", () => {
  it("throws AuthenticationError with status 401 on 401", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ detail: "Invalid API key" }, 401),
    );
    const client = createClient(mockFetch);

    try {
      await client.system.health();
      expect.fail("Should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(AuthenticationError);
      expect((err as AuthenticationError).status).toBe(401);
    }
  });

  it("throws AuthenticationError with status 403 on 403", async () => {
    // 401 (retry with credentials) and 403 (do not retry — escalate) demand
    // opposite consumer handling; the status must survive the mapping.
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ detail: "Forbidden" }, 403),
    );
    const client = createClient(mockFetch);

    try {
      await client.system.health();
      expect.fail("Should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(AuthenticationError);
      expect((err as AuthenticationError).status).toBe(403);
    }
  });

  it("throws DomainRestrictedError on 403 with retrieval_reason", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse(
        { detail: { retrieval_reason: "consumer_domain_restricted", retrieval_skipped: true } },
        403,
      ),
    );
    const client = createClient(mockFetch);
    try {
      await client.kb.query({ query: "x", domains: ["finance"] });
      expect.fail("Should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(DomainRestrictedError);
    }
  });

  it("throws ValidationError on 422", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ detail: "query is required" }, 422),
    );
    const client = createClient(mockFetch);
    await expect(client.kb.query({ query: "" } as never)).rejects.toThrow(ValidationError);
  });

  it("throws RateLimitError on 429", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ detail: "Too many requests" }, 429),
    );
    const client = createClient(mockFetch);
    await expect(client.system.health()).rejects.toThrow(RateLimitError);
  });

  it("throws NotFoundError on 404", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ detail: "Not found" }, 404),
    );
    const client = createClient(mockFetch);
    await expect(client.system.health()).rejects.toThrow(NotFoundError);
  });

  it("throws ServiceUnavailableError on 503", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ detail: "ChromaDB down" }, 503),
    );
    const client = createClient(mockFetch);
    await expect(client.system.health()).rejects.toThrow(ServiceUnavailableError);
  });

  it("throws CeridSDKError on other status codes", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ detail: "Internal error" }, 500),
    );
    const client = createClient(mockFetch);
    await expect(client.system.health()).rejects.toThrow(CeridSDKError);
  });

  it("preserves error body for inspection", async () => {
    const errorBody = { detail: "Rate limit exceeded", retry_after: 30 };
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(errorBody, 429));
    const client = createClient(mockFetch);

    try {
      await client.system.health();
      expect.fail("Should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(RateLimitError);
      expect((err as RateLimitError).status).toBe(429);
      expect((err as RateLimitError).body).toEqual(errorBody);
    }
  });
});

// ---------------------------------------------------------------------------
// Async memory extract: 200 vs 202 are different shapes, not the same one.
// ---------------------------------------------------------------------------

describe("memory.extract discriminates the 202 accepted envelope", () => {
  it("returns the accepted envelope with its job_id on 202", async () => {
    const accepted = {
      job_id: "job-42",
      status: "queued",
      status_url: "/sdk/v1/memory/extract/jobs/job-42",
      conversation_id: "conv-1",
    };
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(accepted, 202));
    const client = createClient(mockFetch);

    const result = await client.memory.extract({
      response_text: "I prefer dark mode.",
      conversation_id: "conv-1",
    });

    expect(
      isMemoryExtractAccepted(result),
      "202 parsed as a finished extraction — the caller never learns a job_id exists",
    ).toBe(true);
    if (!isMemoryExtractAccepted(result)) throw new Error("unreachable");
    expect(result.job_id).toBe("job-42");
    expect(result.status_url).toContain("job-42");
  });

  it("does not let a caller read extraction counts without branching", async () => {
    // Layer 1 (compile-time): `extract()` returns a union, so reaching for a
    // sync-only field without narrowing is a type error. Before the 202 was
    // given its own return type this compiled fine and silently reported
    // "0 memories stored" for work that was queued and would succeed.
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({}, 202));
    const client = createClient(mockFetch);
    const result = await client.memory.extract({
      response_text: "x",
      conversation_id: "conv-1",
    });

    // @ts-expect-error - on the union this is `unknown`, not `number`
    const stored: number = result.memories_stored;
    void stored;

    expect(result).toBeDefined();
  });

  it("returns the sync result on 200", async () => {
    const body = {
      conversation_id: "conv-1", timestamp: "", memories_extracted: 2,
      memories_stored: 2, skipped_duplicates: 0, results: [],
    };
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(body));
    const client = createClient(mockFetch);

    const result = await client.memory.extract({
      response_text: "I prefer dark mode.",
      conversation_id: "conv-1",
    });

    expect(isMemoryExtractAccepted(result)).toBe(false);
    if (isMemoryExtractAccepted(result)) throw new Error("unreachable");
    expect(result.memories_stored).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// Retry-After: the server sets it on both the 429 rate-limit path and the 503
// SLO-budget path, and the guide tells consumers to back off on it. The
// Response is consumed inside raiseForStatus, so if the typed error doesn't
// carry the header the value is gone by the time the caller sees the failure.
// ---------------------------------------------------------------------------

function errorResponse(body: unknown, status: number, headers: Record<string, string>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("Retry-After reaches the caller", () => {
  it("RateLimitError carries the parsed Retry-After", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      errorResponse({ detail: "Rate limited" }, 429, { "Retry-After": "30" }),
    );
    const client = createClient(mockFetch);

    await expect(client.system.health()).rejects.toMatchObject({
      name: "RateLimitError",
      retryAfter: 30,
    });
  });

  it("ServiceUnavailableError carries the parsed Retry-After", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      errorResponse({ detail: "SLO budget exhausted" }, 503, { "Retry-After": "12.5" }),
    );
    const client = createClient(mockFetch);

    await expect(client.system.health()).rejects.toMatchObject({
      name: "ServiceUnavailableError",
      retryAfter: 12.5,
    });
  });

  it("leaves retryAfter null when the server sends no header", async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ detail: "Rate limited" }, 429));
    const client = createClient(mockFetch);

    await expect(client.system.health()).rejects.toMatchObject({ retryAfter: null });
  });
});

// ---------------------------------------------------------------------------
// Protocol-version enforcement. The constant is the compatibility contract;
// the health / settings responses are the only place the server states its own
// protocol version, so that is where the comparison happens — no extra round
// trip, and no silent payload skew against a server that moved on.
// ---------------------------------------------------------------------------

const SDK_MAJOR = Number(SDK_PROTOCOL_VERSION.split(".")[0]);
const OTHER_MAJOR = SDK_MAJOR + 1;

describe("Protocol version enforcement", () => {
  for (const method of ["health", "healthDetailed", "settings"] as const) {
    it(`system.${method}() rejects a different major protocol version`, async () => {
      const mockFetch = vi.fn().mockResolvedValue(
        jsonResponse({ status: "healthy", version: `${OTHER_MAJOR}.0.0`, services: {}, features: {}, tier: "community" }),
      );
      const client = createClient(mockFetch);

      await expect(client.system[method]()).rejects.toMatchObject({
        name: "ProtocolVersionError",
        serverVersion: `${OTHER_MAJOR}.0.0`,
      });
    });
  }

  it("accepts any minor/patch on the same major", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ status: "healthy", version: `${SDK_MAJOR}.99.4`, services: {}, features: {} }),
    );
    const client = createClient(mockFetch);
    await expect(client.system.health()).resolves.toMatchObject({ version: `${SDK_MAJOR}.99.4` });
  });

  it("treats an unstated version as unknown, not incompatible", async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ tier: "community", features: {} }));
    const client = createClient(mockFetch);
    await expect(client.system.settings()).resolves.toBeDefined();
  });

  it("exports ProtocolVersionError as a CeridSDKError", () => {
    expect(new ProtocolVersionError("x", "2.0.0")).toBeInstanceOf(CeridSDKError);
  });
});
