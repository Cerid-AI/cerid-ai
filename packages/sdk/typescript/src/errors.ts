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

  constructor(message: string, status: number, body?: unknown) {
    super(message);
    this.name = "CeridSDKError";
    this.status = status;
    this.body = body;
  }
}

export class AuthenticationError extends CeridSDKError {
  constructor(message = "Authentication failed", body?: unknown) {
    super(message, 401, body);
    this.name = "AuthenticationError";
  }
}

export class RateLimitError extends CeridSDKError {
  constructor(message = "Rate limit exceeded", body?: unknown) {
    super(message, 429, body);
    this.name = "RateLimitError";
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
  constructor(message = "Service unavailable", body?: unknown) {
    super(message, 503, body);
    this.name = "ServiceUnavailableError";
  }
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

  const detail =
    typeof body === "object" && body !== null && "detail" in body
      ? String((body as Record<string, unknown>).detail)
      : `HTTP ${response.status}`;

  switch (response.status) {
    case 401:
    case 403:
      throw new AuthenticationError(detail, body);
    case 404:
      throw new NotFoundError(detail, body);
    case 422:
      throw new ValidationError(detail, body);
    case 429:
      throw new RateLimitError(detail, body);
    case 503:
      throw new ServiceUnavailableError(detail, body);
    default:
      throw new CeridSDKError(detail, response.status, body);
  }
}
