// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CeridClient — zero-dependency typed client for the Cerid AI SDK API.
 *
 * Uses native fetch() and groups endpoints into resource objects that
 * mirror the server-side SDK router structure:
 *
 *   client.kb.query()           — POST /sdk/v1/query
 *   client.kb.search()          — POST /sdk/v1/search
 *   client.kb.ingest()          — POST /sdk/v1/ingest
 *   client.kb.ingestFile()      — POST /sdk/v1/ingest/file
 *   client.kb.ingestExternal()  — POST /sdk/v1/ingest/external
 *   client.kb.collections()     — GET  /sdk/v1/collections
 *   client.kb.taxonomy()        — GET  /sdk/v1/taxonomy
 *   client.verify.check()       — POST /sdk/v1/hallucination
 *   client.memory.extract()     — POST /sdk/v1/memory/extract (200 sync | 202 queued)
 *   client.memory.getJob()      — GET  /sdk/v1/memory/extract/jobs/{job_id}
 *   client.llm.complete()       — POST /sdk/v1/llm/complete
 *   client.system.health()      — GET  /sdk/v1/health
 *   client.system.healthDetailed() — GET /sdk/v1/health/detailed
 *   client.system.settings()    — GET  /sdk/v1/settings
 *   client.system.plugins()     — GET  /sdk/v1/plugins
 */

import { ProtocolVersionError, raiseForStatus } from "./errors.js";
import { SDK_PROTOCOL_VERSION } from "./version.js";
import type {
  CeridClientOptions,
  CollectionsResponse,
  DeleteArtifactResponse,
  DetailedHealthResponse,
  HallucinationCheckRequest,
  HallucinationResponse,
  HealthResponse,
  IngestExternalRequest,
  IngestExternalResponse,
  IngestFileRequest,
  IngestRequest,
  IngestResponse,
  LLMCompleteRequest,
  LLMCompleteResponse,
  MemoryExtractAcceptedResponse,
  MemoryExtractJobStatus,
  MemoryExtractRequest,
  MemoryExtractResponse,
  MemoryRecallRequest,
  MemoryRecallResponse,
  PluginListResponse,
  QueryRequest,
  QueryResponse,
  RequestOptions,
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
    protected readonly _timeoutMs?: number,
  ) {}

  private _signal(opts?: RequestOptions): AbortSignal | undefined {
    const ms = opts?.timeoutMs ?? this._timeoutMs;
    return ms ? AbortSignal.timeout(ms) : undefined;
  }

  private _jsonHeaders(opts?: RequestOptions, write = false): Record<string, string> {
    const headers: Record<string, string> = { ...this._headers, "Content-Type": "application/json" };
    if (write) {
      headers["Idempotency-Key"] = opts?.idempotencyKey ?? crypto.randomUUID();
    }
    return headers;
  }

  protected async _get<T>(path: string, opts?: RequestOptions): Promise<T> {
    const response = await this._fetch(`${this._baseUrl}${path}`, {
      method: "GET",
      headers: this._headers,
      signal: this._signal(opts),
    });
    await raiseForStatus(response);
    return (await response.json()) as T;
  }

  protected async _post<T>(path: string, body: unknown, opts?: RequestOptions, write = false): Promise<T> {
    return (await this._postWithStatus<T>(path, body, opts, write)).data;
  }

  /** POST that keeps the HTTP status, for routes whose 2xx codes mean
   * different things (memory/extract answers 200 *or* 202). */
  protected async _postWithStatus<T>(
    path: string,
    body: unknown,
    opts?: RequestOptions,
    write = false,
  ): Promise<{ status: number; data: T }> {
    const response = await this._fetch(`${this._baseUrl}${path}`, {
      method: "POST",
      headers: this._jsonHeaders(opts, write),
      body: JSON.stringify(body),
      signal: this._signal(opts),
    });
    await raiseForStatus(response);
    return { status: response.status, data: (await response.json()) as T };
  }

  protected async _delete<T>(path: string, opts?: RequestOptions): Promise<T> {
    const headers: Record<string, string> = {
      ...this._headers,
      "Idempotency-Key": opts?.idempotencyKey ?? crypto.randomUUID(),
    };
    const response = await this._fetch(`${this._baseUrl}${path}`, {
      method: "DELETE",
      headers,
      signal: this._signal(opts),
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
  async query(params: QueryRequest, opts?: RequestOptions): Promise<QueryResponse> {
    return this._post<QueryResponse>("/sdk/v1/query", params, opts);
  }

  /** Direct vector search without agent orchestration. */
  async search(params: SearchRequest): Promise<SearchResponse> {
    return this._post<SearchResponse>("/sdk/v1/search", params);
  }

  /** Ingest raw text content into the knowledge base. */
  async ingest(params: IngestRequest, opts?: RequestOptions): Promise<IngestResponse> {
    return this._post<IngestResponse>("/sdk/v1/ingest", params, opts, true);
  }

  /** Ingest a file from the archive or an absolute path. */
  async ingestFile(params: IngestFileRequest, opts?: RequestOptions): Promise<IngestResponse> {
    return this._post<IngestResponse>("/sdk/v1/ingest/file", params, opts, true);
  }

  /** Multipart file ingest via POST /sdk/v1/ingest/upload. */
  async ingestBytes(
    filename: string,
    content: Blob | ArrayBuffer | Uint8Array,
    params?: { domain?: string; tags?: string; sub_category?: string; categorize_mode?: string },
    opts?: RequestOptions,
  ): Promise<IngestResponse> {
    const form = new FormData();
    const blob =
      content instanceof Blob
        ? content
        : new Blob([content as BlobPart]);
    form.append("file", blob, filename);
    const url = new URL(`${this._baseUrl}/sdk/v1/ingest/upload`);
    if (params?.domain) url.searchParams.set("domain", params.domain);
    if (params?.tags) url.searchParams.set("tags", params.tags);
    if (params?.sub_category) url.searchParams.set("sub_category", params.sub_category);
    if (params?.categorize_mode) url.searchParams.set("categorize_mode", params.categorize_mode);
    const headers: Record<string, string> = { ...this._headers };
    headers["Idempotency-Key"] = opts?.idempotencyKey ?? crypto.randomUUID();
    delete headers["Content-Type"];
    const ms = opts?.timeoutMs ?? this._timeoutMs;
    const response = await this._fetch(url.toString(), {
      method: "POST",
      headers,
      body: form,
      signal: ms ? AbortSignal.timeout(ms) : undefined,
    });
    await raiseForStatus(response);
    return (await response.json()) as IngestResponse;
  }

  /** Delete one artifact if the consumer is allowed its domain. */
  async deleteArtifact(artifactId: string, opts?: RequestOptions): Promise<DeleteArtifactResponse> {
    return this._delete<DeleteArtifactResponse>(
      `/sdk/v1/artifacts/${encodeURIComponent(artifactId)}`,
      opts,
    );
  }

  /**
   * Adapter-shaped ingest for external services (Readwise, Pocket,
   * Telegram-bot, …). The caller supplies a `field_mappings` config that
   * declares how to extract canonical fields from the raw `payload`.
   * See `docs/INTEGRATION_GUIDE.md` for per-service mapping examples.
   */
  async ingestExternal(params: IngestExternalRequest): Promise<IngestExternalResponse> {
    return this._post<IngestExternalResponse>("/sdk/v1/ingest/external", params);
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
  async check(params: HallucinationCheckRequest, opts?: RequestOptions): Promise<HallucinationResponse> {
    return this._post<HallucinationResponse>("/sdk/v1/hallucination", params, opts);
  }
}

export class MemoryResource extends BaseResource {
  /**
   * Extract memories from conversation text and store as KB artifacts.
   *
   * Resolves to a `MemoryExtractResponse` when the server ran the extraction
   * inline (200), or a `MemoryExtractAcceptedResponse` when it enqueued the
   * work (202 — the default on servers with `MEMORY_QUEUE_MODE=async`).
   * Narrow with `isMemoryExtractAccepted` and poll `getJob(job_id)`:
   *
   * ```ts
   * const result = await client.memory.extract({ response_text, conversation_id });
   * if (isMemoryExtractAccepted(result)) {
   *   const job = await client.memory.getJob(result.job_id);
   * }
   * ```
   */
  async extract(
    params: MemoryExtractRequest,
    opts?: RequestOptions,
  ): Promise<MemoryExtractResponse | MemoryExtractAcceptedResponse> {
    const { status, data } = await this._postWithStatus<
      MemoryExtractResponse | MemoryExtractAcceptedResponse
    >("/sdk/v1/memory/extract", params, opts, true);
    return status === 202
      ? (data as MemoryExtractAcceptedResponse)
      : (data as MemoryExtractResponse);
  }

  /** Salience-aware memory recall. */
  async recall(params: MemoryRecallRequest, opts?: RequestOptions): Promise<MemoryRecallResponse> {
    return this._post<MemoryRecallResponse>("/sdk/v1/memory/recall", params, opts);
  }

  /**
   * Poll an async memory_extract job by `job_id`. When the server is in
   * `MEMORY_QUEUE_MODE=async`, `extract()` may return a 202 Accepted
   * envelope with a `job_id`; use this method to poll for completion.
   * Status transitions: queued → started → finished | failed.
   */
  async getJob(jobId: string): Promise<MemoryExtractJobStatus> {
    return this._get<MemoryExtractJobStatus>(`/sdk/v1/memory/extract/jobs/${encodeURIComponent(jobId)}`);
  }
}

export class LLMResource extends BaseResource {
  /**
   * Smart-routed LLM completion. The server's `smart_router` selects a
   * model tier (FREE / CHEAP / CAPABLE / RESEARCH / EXPERT) based on
   * `task_type`, `query` complexity, and `cost_sensitivity` (how sensitive
   * you are to spend: `high` is cheapest, `low` most capable). When
   * `slo_budget_ms` is set, tiers whose empirical p95 exceeds the budget
   * are filtered out — if none fits, the response is HTTP 503 with a
   * `Retry-After` header carrying the floor p95.
   */
  async complete(params: LLMCompleteRequest, opts?: RequestOptions): Promise<LLMCompleteResponse> {
    return this._post<LLMCompleteResponse>("/sdk/v1/llm/complete", params, opts);
  }
}

export class SystemResource extends BaseResource {
  /**
   * Compare the server's stated wire protocol against this client's pin.
   * These three responses are the only place the server states it, so the
   * check costs no extra round trip. A server that states no usable version
   * is unknown, not incompatible.
   */
  private _assertProtocolCompatible(serverVersion: unknown): void {
    if (typeof serverVersion !== "string" || serverVersion.length === 0) return;
    if (serverVersion.split(".")[0] === SDK_PROTOCOL_VERSION.split(".")[0]) return;
    throw new ProtocolVersionError(
      `Server wire protocol ${serverVersion} is incompatible with this SDK, `
        + `which was built against ${SDK_PROTOCOL_VERSION}. Upgrade @cerid-ai/sdk `
        + "to a release that targets the server's major version.",
      serverVersion,
    );
  }

  /** Service health with feature flags. */
  async health(opts?: RequestOptions): Promise<HealthResponse> {
    const result = await this._get<HealthResponse>("/sdk/v1/health", opts);
    this._assertProtocolCompatible(result.version);
    return result;
  }

  /** Extended health check with circuit breaker states and uptime. */
  async healthDetailed(): Promise<DetailedHealthResponse> {
    const result = await this._get<DetailedHealthResponse>("/sdk/v1/health/detailed");
    this._assertProtocolCompatible(result.version);
    return result;
  }

  /** Read-only server configuration: version, tier, and feature flags. */
  async settings(): Promise<SettingsResponse> {
    const result = await this._get<SettingsResponse>("/sdk/v1/settings");
    this._assertProtocolCompatible(result.version);
    return result;
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
  public readonly llm: LLMResource;

  constructor(options: CeridClientOptions) {
    const baseUrl = options.baseUrl.replace(/\/+$/, "");
    const fetchFn = options.fetch ?? globalThis.fetch;

    const headers: Record<string, string> = {
      "X-Client-ID": options.clientId,
    };
    if (options.apiKey) {
      headers["X-API-Key"] = options.apiKey;
    }

    this.kb = new KBResource(baseUrl, headers, fetchFn, options.timeoutMs);
    this.verify = new VerifyResource(baseUrl, headers, fetchFn, options.timeoutMs);
    this.memory = new MemoryResource(baseUrl, headers, fetchFn, options.timeoutMs);
    this.system = new SystemResource(baseUrl, headers, fetchFn, options.timeoutMs);
    this.llm = new LLMResource(baseUrl, headers, fetchFn, options.timeoutMs);
  }
}
