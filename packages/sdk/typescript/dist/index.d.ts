/**
 * TypeScript interfaces matching the server-side SDK response models
 * defined in src/mcp/models/sdk.py.
 *
 * All response types allow extra fields (index signature) for forward
 * compatibility — the server uses `extra="allow"` on its Pydantic models.
 */
interface CeridClientOptions {
    /** Base URL of the Cerid MCP server, e.g. "http://localhost:8888" */
    baseUrl: string;
    /** X-Client-ID header for per-client rate limiting and domain scoping */
    clientId: string;
    /** Optional API key (X-API-Key header). Only required when server has CERID_API_KEY set. */
    apiKey?: string;
    /** Optional custom fetch implementation (defaults to globalThis.fetch). */
    fetch?: typeof globalThis.fetch;
}
interface QueryRequest {
    query: string;
    domains?: string[] | null;
    top_k?: number;
    use_reranking?: boolean;
    conversation_messages?: Array<{
        role: string;
        content: string;
    }> | null;
    response_text?: string | null;
    model?: string | null;
    enable_self_rag?: boolean | null;
    strict_domains?: boolean | null;
    rag_mode?: string | null;
    source_config?: Record<string, unknown> | null;
}
interface HallucinationCheckRequest {
    response_text: string;
    conversation_id: string;
    threshold?: number | null;
    model?: string | null;
}
interface MemoryExtractRequest {
    response_text: string;
    conversation_id: string;
    model?: string;
}
interface IngestRequest {
    content: string;
    domain?: string;
    tags?: string;
}
interface IngestFileRequest {
    file_path: string;
    domain?: string;
    tags?: string;
    categorize_mode?: string;
}
interface SearchRequest {
    query: string;
    domain?: string;
    top_k?: number;
}
interface QueryResponse {
    context: string;
    sources: Array<Record<string, unknown>>;
    confidence: number;
    domains_searched: string[];
    total_results: number;
    token_budget_used: number;
    graph_results: number;
    results: Array<Record<string, unknown>>;
    [key: string]: unknown;
}
interface HallucinationResponse {
    conversation_id: string;
    timestamp: string;
    skipped: boolean;
    reason: string | null;
    claims: Array<Record<string, unknown>>;
    summary: {
        total: number;
        verified: number;
        unverified: number;
        uncertain: number;
    };
    [key: string]: unknown;
}
interface MemoryExtractResponse {
    conversation_id: string;
    timestamp: string;
    memories_extracted: number;
    memories_stored: number;
    skipped_duplicates: number;
    results: Array<Record<string, unknown>>;
    [key: string]: unknown;
}
interface HealthResponse {
    status: string;
    version: string;
    services: Record<string, string>;
    features: Record<string, boolean>;
    [key: string]: unknown;
}
interface DetailedHealthResponse extends HealthResponse {
    circuit_breakers: Record<string, string>;
    degradation_tier: string;
    uptime_seconds: number;
}
interface IngestResponse {
    status: string;
    artifact_id: string;
    chunks: number;
    domain: string;
    [key: string]: unknown;
}
interface CollectionsResponse {
    collections: string[];
    total: number;
    [key: string]: unknown;
}
interface TaxonomyResponse {
    domains: string[];
    taxonomy: Record<string, unknown>;
    [key: string]: unknown;
}
interface SettingsResponse {
    version: string;
    tier: string;
    features: Record<string, boolean>;
    [key: string]: unknown;
}
interface SearchResponse {
    results: Array<Record<string, unknown>>;
    total_results: number;
    confidence: number;
    [key: string]: unknown;
}
interface PluginListResponse {
    plugins: Array<Record<string, unknown>>;
    total: number;
    [key: string]: unknown;
}

declare class BaseResource {
    protected readonly _baseUrl: string;
    protected readonly _headers: Record<string, string>;
    protected readonly _fetch: typeof globalThis.fetch;
    constructor(_baseUrl: string, _headers: Record<string, string>, _fetch: typeof globalThis.fetch);
    protected _get<T>(path: string): Promise<T>;
    protected _post<T>(path: string, body: unknown): Promise<T>;
}
declare class KBResource extends BaseResource {
    /** Multi-domain knowledge base search with hybrid BM25+vector retrieval. */
    query(params: QueryRequest): Promise<QueryResponse>;
    /** Direct vector search without agent orchestration. */
    search(params: SearchRequest): Promise<SearchResponse>;
    /** Ingest raw text content into the knowledge base. */
    ingest(params: IngestRequest): Promise<IngestResponse>;
    /** Ingest a file from the archive or an absolute path. */
    ingestFile(params: IngestFileRequest): Promise<IngestResponse>;
    /** List all knowledge base collections. */
    collections(): Promise<CollectionsResponse>;
    /** Get the domain taxonomy tree. */
    taxonomy(): Promise<TaxonomyResponse>;
}
declare class VerifyResource extends BaseResource {
    /** Verify factual claims in a response against the KB. */
    check(params: HallucinationCheckRequest): Promise<HallucinationResponse>;
}
declare class MemoryResource extends BaseResource {
    /** Extract memories from conversation text and store as KB artifacts. */
    extract(params: MemoryExtractRequest): Promise<MemoryExtractResponse>;
}
declare class SystemResource extends BaseResource {
    /** Service health with feature flags. */
    health(): Promise<HealthResponse>;
    /** Extended health check with circuit breaker states and uptime. */
    healthDetailed(): Promise<DetailedHealthResponse>;
    /** Read-only server configuration: version, tier, and feature flags. */
    settings(): Promise<SettingsResponse>;
    /** List all loaded plugins with status and capabilities. */
    plugins(): Promise<PluginListResponse>;
}
declare class CeridClient {
    readonly kb: KBResource;
    readonly verify: VerifyResource;
    readonly memory: MemoryResource;
    readonly system: SystemResource;
    constructor(options: CeridClientOptions);
}

/**
 * Typed error hierarchy for the Cerid SDK.
 *
 * Mirrors the server-side CeridError hierarchy so consumers can catch
 * specific failure modes without inspecting raw HTTP status codes.
 */
declare class CeridSDKError extends Error {
    readonly status: number;
    readonly body: unknown;
    constructor(message: string, status: number, body?: unknown);
}
declare class AuthenticationError extends CeridSDKError {
    constructor(message?: string, body?: unknown);
}
declare class RateLimitError extends CeridSDKError {
    constructor(message?: string, body?: unknown);
}
declare class ValidationError extends CeridSDKError {
    constructor(message?: string, body?: unknown);
}
declare class NotFoundError extends CeridSDKError {
    constructor(message?: string, body?: unknown);
}
declare class ServiceUnavailableError extends CeridSDKError {
    constructor(message?: string, body?: unknown);
}
/**
 * Inspect an HTTP response and throw a typed error for non-2xx status codes.
 */
declare function raiseForStatus(response: Response): Promise<void>;

export { AuthenticationError, CeridClient, type CeridClientOptions, CeridSDKError, type CollectionsResponse, type DetailedHealthResponse, type HallucinationCheckRequest, type HallucinationResponse, type HealthResponse, type IngestFileRequest, type IngestRequest, type IngestResponse, KBResource, type MemoryExtractRequest, type MemoryExtractResponse, MemoryResource, NotFoundError, type PluginListResponse, type QueryRequest, type QueryResponse, RateLimitError, type SearchRequest, type SearchResponse, ServiceUnavailableError, type SettingsResponse, SystemResource, type TaxonomyResponse, ValidationError, VerifyResource, raiseForStatus };
