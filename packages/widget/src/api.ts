// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Fetch helper for /sdk/v1/query.
 *
 * Features:
 * - AbortSignal support for navigation-away cancellation
 * - One retry on 503 (transient backend unavailability)
 * - 30 s default timeout
 * - No external dependencies
 */

import type { SDKQueryRequest, SDKQueryResponse } from "./types.js";

declare const __DEBUG__: boolean;

const DEFAULT_TIMEOUT_MS = 30_000;
const RETRYABLE_STATUS = new Set([503, 502, 504]);

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface FetchOptions {
  signal?: AbortSignal;
  token?: string;
  timeoutMs?: number;
}

/**
 * POST /sdk/v1/query with one retry on transient errors.
 * Throws ApiError on non-retryable HTTP errors, or the original Error on abort/network failure.
 */
export async function fetchQuery(
  host: string,
  body: SDKQueryRequest,
  opts: FetchOptions = {},
): Promise<SDKQueryResponse> {
  const url = `${host.replace(/\/+$/, "")}/sdk/v1/query`;
  const { signal, token, timeoutMs = DEFAULT_TIMEOUT_MS } = opts;

  // Compose a combined signal: external abort + internal timeout
  const timeout = AbortSignal.timeout(timeoutMs);
  const combined = signal
    ? AbortSignal.any([signal, timeout])
    : timeout;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const attempt = async (): Promise<Response> => {
    return fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: combined,
    });
  };

  let response: Response;
  try {
    response = await attempt();
  } catch (err) {
    // Don't retry on abort
    throw err;
  }

  // One retry on transient errors
  if (RETRYABLE_STATUS.has(response.status)) {
    if (__DEBUG__) {
      console.debug(`[cerid-widget] Retrying ${url} after ${response.status}`);
    }
    try {
      response = await attempt();
    } catch (err) {
      throw err;
    }
  }

  if (!response.ok) {
    throw new ApiError(
      response.status,
      `Cerid API error: ${response.status} ${response.statusText}`,
    );
  }

  return response.json() as Promise<SDKQueryResponse>;
}
