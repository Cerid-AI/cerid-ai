// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CeridClient — zero-dependency typed client for the Cerid AI SDK API.
 *
 * Uses native fetch() and groups endpoints into resource objects that
 * mirror the server-side SDK router structure:
 *
 *   client.kb.query()        — POST /sdk/v1/query
 *   client.kb.search()       — POST /sdk/v1/search
 *   client.kb.ingest()       — POST /sdk/v1/ingest
 *   client.kb.ingestFile()   — POST /sdk/v1/ingest/file
 *   client.kb.collections()  — GET  /sdk/v1/collections
 *   client.kb.taxonomy()     — GET  /sdk/v1/taxonomy
 *   client.verify.check()    — POST /sdk/v1/hallucination
 *   client.memory.extract()  — POST /sdk/v1/memory/extract
 *   client.system.health()   — GET  /sdk/v1/health
 *   client.system.healthDetailed() — GET /sdk/v1/health/detailed
 *   client.system.settings() — GET  /sdk/v1/settings
 *   client.system.plugins()  — GET  /sdk/v1/plugins
 */

import { raiseForStatus } from "./errors.js";
import type {
  CeridClientOptions,
  CollectionsResponse,
  DetailedHealthResponse,
  HallucinationCheckRequest,
  HallucinationResponse,
  HealthResponse,
  IngestFileRequest,
  IngestRequest,
  IngestResponse,
  MemoryExtractRequest,
  MemoryExtractResponse,
  PluginListResponse,
  QueryRequest,
  QueryResponse,
  SearchRequest,
  SearchResponse,
  SettingsResponse,
  TaxonomyResponse,
} from "./types.js";

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

class BaseResource {
  constructor(
    protected readonly _baseUrl: string,
    protected readonly _headers: Record<string, string>,
    protected readonly _fetch: typeof globalThis.fetch,
  ) {}

  protected async _get<T>(path: string): Promise<T> {
    const response = await this._fetch(`${this._baseUrl}${path}`, {
      method: "GET",
      headers: this._headers,
    });
    await raiseForStatus(response);
    return (await response.json()) as T;
  }

  protected async _post<T>(path: string, body: unknown): Promise<T> {
    const response = await this._fetch(`${this._baseUrl}${path}`, {
      method: "POST",
      headers: { ...this._headers, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await raiseForStatus(response);
    return (await response.json()) as T;
  }
}

// ---------------------------------------------------------------------------
// Resource groups
// ---------------------------------------------------------------------------

export class KBResource extends BaseResource {
  /** Multi-domain knowledge base search with hybrid BM25+vector retrieval. */
  async query(params: QueryRequest): Promise<QueryResponse> {
    return this._post<QueryResponse>("/sdk/v1/query", params);
  }

  /** Direct vector search without agent orchestration. */
  async search(params: SearchRequest): Promise<SearchResponse> {
    return this._post<SearchResponse>("/sdk/v1/search", params);
  }

  /** Ingest raw text content into the knowledge base. */
  async ingest(params: IngestRequest): Promise<IngestResponse> {
    return this._post<IngestResponse>("/sdk/v1/ingest", params);
  }

  /** Ingest a file from the archive or an absolute path. */
  async ingestFile(params: IngestFileRequest): Promise<IngestResponse> {
    return this._post<IngestResponse>("/sdk/v1/ingest/file", params);
  }

  /** List all knowledge base collections. */
  async collections(): Promise<CollectionsResponse> {
    return this._get<CollectionsResponse>("/sdk/v1/collections");
  }

  /** Get the domain taxonomy tree. */
  async taxonomy(): Promise<TaxonomyResponse> {
    return this._get<TaxonomyResponse>("/sdk/v1/taxonomy");
  }
}

export class VerifyResource extends BaseResource {
  /** Verify factual claims in a response against the KB. */
  async check(params: HallucinationCheckRequest): Promise<HallucinationResponse> {
    return this._post<HallucinationResponse>("/sdk/v1/hallucination", params);
  }
}

export class MemoryResource extends BaseResource {
  /** Extract memories from conversation text and store as KB artifacts. */
  async extract(params: MemoryExtractRequest): Promise<MemoryExtractResponse> {
    return this._post<MemoryExtractResponse>("/sdk/v1/memory/extract", params);
  }
}

export class SystemResource extends BaseResource {
  /** Service health with feature flags. */
  async health(): Promise<HealthResponse> {
    return this._get<HealthResponse>("/sdk/v1/health");
  }

  /** Extended health check with circuit breaker states and uptime. */
  async healthDetailed(): Promise<DetailedHealthResponse> {
    return this._get<DetailedHealthResponse>("/sdk/v1/health/detailed");
  }

  /** Read-only server configuration: version, tier, and feature flags. */
  async settings(): Promise<SettingsResponse> {
    return this._get<SettingsResponse>("/sdk/v1/settings");
  }

  /** List all loaded plugins with status and capabilities. */
  async plugins(): Promise<PluginListResponse> {
    return this._get<PluginListResponse>("/sdk/v1/plugins");
  }
}

// ---------------------------------------------------------------------------
// Main client
// ---------------------------------------------------------------------------

export class CeridClient {
  public readonly kb: KBResource;
  public readonly verify: VerifyResource;
  public readonly memory: MemoryResource;
  public readonly system: SystemResource;

  constructor(options: CeridClientOptions) {
    const baseUrl = options.baseUrl.replace(/\/+$/, "");
    const fetchFn = options.fetch ?? globalThis.fetch;

    const headers: Record<string, string> = {
      "X-Client-ID": options.clientId,
    };
    if (options.apiKey) {
      headers["X-API-Key"] = options.apiKey;
    }

    this.kb = new KBResource(baseUrl, headers, fetchFn);
    this.verify = new VerifyResource(baseUrl, headers, fetchFn);
    this.memory = new MemoryResource(baseUrl, headers, fetchFn);
    this.system = new SystemResource(baseUrl, headers, fetchFn);
  }
}
