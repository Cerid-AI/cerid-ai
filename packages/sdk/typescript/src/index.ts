// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * @cerid-ai/sdk — Zero-dependency typed client for the Cerid AI SDK API.
 *
 * @example
 * ```ts
 * import { CeridClient } from "@cerid-ai/sdk";
 *
 * const client = new CeridClient({
 *   baseUrl: "http://localhost:8888",
 *   clientId: "my-app",
 * });
 *
 * const result = await client.kb.query({ query: "What is RAG?" });
 * console.log(result.context);
 * ```
 *
 * @packageDocumentation
 */

// Client
export { CeridClient, KBResource, VerifyResource, MemoryResource, SystemResource } from "./client.js";

// Types
export type {
  CeridClientOptions,
  QueryRequest,
  QueryResponse,
  HallucinationCheckRequest,
  HallucinationResponse,
  MemoryExtractRequest,
  MemoryExtractResponse,
  HealthResponse,
  DetailedHealthResponse,
  IngestRequest,
  IngestFileRequest,
  IngestResponse,
  CollectionsResponse,
  TaxonomyResponse,
  SettingsResponse,
  SearchRequest,
  SearchResponse,
  PluginListResponse,
} from "./types.js";

// Errors
export {
  CeridSDKError,
  AuthenticationError,
  RateLimitError,
  ValidationError,
  NotFoundError,
  ServiceUnavailableError,
  raiseForStatus,
} from "./errors.js";
