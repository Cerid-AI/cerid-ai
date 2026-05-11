// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fetchQuery, ApiError } from "../src/api.js";
import type { SDKQueryResponse } from "../src/types.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeOkResponse(body: SDKQueryResponse): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function makeStatusResponse(status: number): Response {
  return new Response(null, { status, statusText: String(status) });
}

const MINIMAL_RESPONSE: SDKQueryResponse = {
  context: "Test context",
  sources: [],
  confidence: 0.9,
  domains_searched: ["finance"],
  total_results: 1,
  token_budget_used: 100,
  graph_results: 0,
  results: [],
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("fetchQuery", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("POSTs to /sdk/v1/query with the correct URL", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(makeOkResponse(MINIMAL_RESPONSE));

    await fetchQuery("https://cerid.example.com", { query: "hello" });

    expect(fetch).toHaveBeenCalledWith(
      "https://cerid.example.com/sdk/v1/query",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("strips trailing slash from host", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(makeOkResponse(MINIMAL_RESPONSE));

    await fetchQuery("https://cerid.example.com///", { query: "hello" });

    expect(fetch).toHaveBeenCalledWith(
      "https://cerid.example.com/sdk/v1/query",
      expect.anything(),
    );
  });

  it("sends Authorization header when token is provided", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(makeOkResponse(MINIMAL_RESPONSE));

    await fetchQuery(
      "https://cerid.example.com",
      { query: "hello" },
      { token: "my-secret-token" },
    );

    const [, init] = vi.mocked(fetch).mock.calls[0]!;
    const headers = init?.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer my-secret-token");
  });

  it("returns the parsed JSON response", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(makeOkResponse(MINIMAL_RESPONSE));

    const result = await fetchQuery("https://cerid.example.com", {
      query: "hello",
    });

    expect(result).toEqual(MINIMAL_RESPONSE);
  });

  it("retries once on 503", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(makeStatusResponse(503))
      .mockResolvedValueOnce(makeOkResponse(MINIMAL_RESPONSE));

    const result = await fetchQuery("https://cerid.example.com", {
      query: "hello",
    });

    expect(fetch).toHaveBeenCalledTimes(2);
    expect(result).toEqual(MINIMAL_RESPONSE);
  });

  it("retries once on 502", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(makeStatusResponse(502))
      .mockResolvedValueOnce(makeOkResponse(MINIMAL_RESPONSE));

    await fetchQuery("https://cerid.example.com", { query: "hello" });

    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("throws ApiError on 404 (no retry)", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(makeStatusResponse(404));

    await expect(
      fetchQuery("https://cerid.example.com", { query: "hello" }),
    ).rejects.toThrow(ApiError);

    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("throws ApiError on 401", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(makeStatusResponse(401));

    const err = await fetchQuery(
      "https://cerid.example.com",
      { query: "hello" },
    ).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(401);
  });

  it("throws ApiError on persistent 503 (both attempts fail)", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(makeStatusResponse(503))
      .mockResolvedValueOnce(makeStatusResponse(503));

    await expect(
      fetchQuery("https://cerid.example.com", { query: "hello" }),
    ).rejects.toThrow(ApiError);

    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("propagates AbortError without retry", async () => {
    const abortErr = new DOMException("Aborted", "AbortError");
    vi.mocked(fetch).mockRejectedValueOnce(abortErr);

    await expect(
      fetchQuery("https://cerid.example.com", { query: "hello" }),
    ).rejects.toThrow("Aborted");

    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("forwards the AbortSignal to fetch", async () => {
    const controller = new AbortController();
    vi.mocked(fetch).mockResolvedValueOnce(makeOkResponse(MINIMAL_RESPONSE));

    await fetchQuery(
      "https://cerid.example.com",
      { query: "hello" },
      { signal: controller.signal },
    );

    const [, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(init?.signal).toBeDefined();
  });
});
