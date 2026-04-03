// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  CeridClient,
  CeridSDKError,
  AuthenticationError,
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
    apiKey: "test-key",
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
  let mockFetch: ReturnType<typeof vi.fn>;
  let client: CeridClient;

  beforeEach(() => {
    mockFetch = vi.fn();
    client = createClient(mockFetch);
  });

  it("query() POSTs to /sdk/v1/query", async () => {
    const body = { context: "result", sources: [], confidence: 0.9, domains_searched: ["general"], total_results: 1, token_budget_used: 100, graph_results: 0, results: [] };
    mockFetch.mockResolvedValue(jsonResponse(body));

    const result = await client.kb.query({ query: "What is RAG?", top_k: 5 });
    expect(result.context).toBe("result");
    expect(result.confidence).toBe(0.9);

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe("http://localhost:8888/sdk/v1/query");
    expect(JSON.parse(init.body)).toEqual({ query: "What is RAG?", top_k: 5 });
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
});

// ---------------------------------------------------------------------------
// System resource
// ---------------------------------------------------------------------------

describe("System resource", () => {
  let mockFetch: ReturnType<typeof vi.fn>;
  let client: CeridClient;

  beforeEach(() => {
    mockFetch = vi.fn();
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
  it("throws AuthenticationError on 401", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ detail: "Invalid API key" }, 401),
    );
    const client = createClient(mockFetch);
    await expect(client.system.health()).rejects.toThrow(AuthenticationError);
  });

  it("throws AuthenticationError on 403", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ detail: "Forbidden" }, 403),
    );
    const client = createClient(mockFetch);
    await expect(client.system.health()).rejects.toThrow(AuthenticationError);
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
