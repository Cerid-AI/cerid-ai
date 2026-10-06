// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Typed error hierarchy for the Cerid SDK.
 *
 * Mirrors the server-side CeridError hierarchy so consumers can catch
 * specific failure modes without inspecting raw HTTP status codes.
 */

export class CeridSDKError extends Error {
  public readonly status: number;
  public readonly body: unknown;
  /**
   * The server's machine-readable `error_code` (for example
   * `CROSS_SITE_REQUEST_REFUSED`), or null when the body has none.
   */
  public errorCode: string | null = null;

  constructor(message: string, status: number, body?: unknown) {
    super(message);
    this.name = "CeridSDKError";
    this.status = status;
    this.body = body;
  }
}

export class DomainRestrictedError extends CeridSDKError {
  constructor(message = "Consumer is not allowed the requested domain", body?: unknown) {
    super(message, 403, body);
    this.name = "DomainRestrictedError";
  }
}

export class AuthenticationError extends CeridSDKError {
  /**
   * Raised on 401 Unauthorized or 403 Forbidden responses. The real status
   * is preserved (parity with the Python SDK) so consumers can distinguish
   * "not authenticated" (401 — retry with credentials) from "authenticated
   * but not permitted" (403 — do not retry).
   */
  constructor(message = "Authentication failed", status = 401, body?: unknown) {
    super(message, status, body);
    this.name = "AuthenticationError";
  }
}

export class RateLimitError extends CeridSDKError {
  /** Seconds to wait before retrying, from the server's `Retry-After` header. */
  public readonly retryAfter: number | null;

  constructor(message = "Rate limit exceeded", body?: unknown, retryAfter: number | null = null) {
    super(message, 429, body);
    this.name = "RateLimitError";
    this.retryAfter = retryAfter;
  }
}

export class ValidationError extends CeridSDKError {
  constructor(message = "Validation error", body?: unknown) {
    super(message, 422, body);
    this.name = "ValidationError";
  }
}

export class NotFoundError extends CeridSDKError {
  constructor(message = "Resource not found", body?: unknown) {
    super(message, 404, body);
    this.name = "NotFoundError";
  }
}

export class ServiceUnavailableError extends CeridSDKError {
  /**
   * Seconds to wait before retrying, from the server's `Retry-After` header.
   * The SLO-budget path sets it to the floor p95 of the cheapest tier that
   * could serve the request.
   */
  public readonly retryAfter: number | null;

  constructor(message = "Service unavailable", body?: unknown, retryAfter: number | null = null) {
    super(message, 503, body);
    this.name = "ServiceUnavailableError";
    this.retryAfter = retryAfter;
  }
}

export class ProtocolVersionError extends CeridSDKError {
  /** The wire-protocol version the server reported. */
  public readonly serverVersion: string;

  constructor(message: string, serverVersion: string) {
    super(message, 0);
    this.name = "ProtocolVersionError";
    this.serverVersion = serverVersion;
  }
}

/** Parse the delay-seconds form of `Retry-After`; null when absent or not a number. */
function parseRetryAfter(raw: string | null): number | null {
  if (!raw) return null;
  const seconds = Number(raw);
  return Number.isFinite(seconds) ? seconds : null;
}

/**
 * Inspect an HTTP response and throw a typed error for non-2xx status codes.
 */
export async function raiseForStatus(response: Response): Promise<void> {
  if (response.ok) return;

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = await response.text().catch(() => null);
  }

  const rawDetail =
    typeof body === "object" && body !== null && "detail" in body
      ? String((body as Record<string, unknown>).detail)
      : `HTTP ${response.status}`;
  const rawCode =
    typeof body === "object" && body !== null ? (body as Record<string, unknown>).error_code : undefined;
  const errorCode = typeof rawCode === "string" && rawCode ? rawCode : null;
  const detail = errorCode ? `${rawDetail} (${errorCode})` : rawDetail;

  const retryAfter = parseRetryAfter(response.headers.get("retry-after"));

  const err = buildError(response.status, detail, body, retryAfter);
  err.errorCode = errorCode;
  throw err;
}

function buildError(status: number, detail: string, body: unknown, retryAfter: number | null): CeridSDKError {
  if (status === 403) {
    const reason =
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof (body as { detail: unknown }).detail === "object" &&
      (body as { detail: { retrieval_reason?: string } }).detail !== null
        ? (body as { detail: { retrieval_reason?: string } }).detail.retrieval_reason
        : undefined;
    if (reason === "consumer_domain_restricted") {
      return new DomainRestrictedError(detail, body);
    }
    return new AuthenticationError(detail, status, body);
  }

  switch (status) {
    case 401:
      return new AuthenticationError(detail, status, body);
    case 404:
      return new NotFoundError(detail, body);
    case 422:
      return new ValidationError(detail, body);
    case 429:
      return new RateLimitError(detail, body, retryAfter);
    case 503:
      return new ServiceUnavailableError(detail, body, retryAfter);
    default:
      return new CeridSDKError(detail, status, body);
  }
}
