export declare class ApiError extends Error {
    readonly status: number;
    constructor(status: number, message: string);
}

export declare class CeridChat extends HTMLElement {
    static readonly observedAttributes: readonly ["host", "token", "placeholder", "theme", "max-claims"];
    private readonly shadow;
    private messagesEl;
    private inputEl;
    private sendBtn;
    private messages;
    private abortController;
    private mediaQuery;
    private mqListener;
    constructor();
    connectedCallback(): void;
    disconnectedCallback(): void;
    attributeChangedCallback(name: string, oldVal: string | null, newVal: string | null): void;
    get host(): string;
    get token(): string | undefined;
    get placeholder(): string;
    get theme(): WidgetTheme;
    get maxClaims(): number;
    private render;
    private buildHeader;
    private buildInputArea;
    private buildFooter;
    private applyTheme;
    private renderEmptyState;
    private showErrorBanner;
    private appendMessage;
    private updateMessageBubble;
    private renderClaims;
    private scrollToBottom;
    private handleSubmit;
    private clearConversation;
}

export declare interface ChatMessage {
    id: string;
    role: "user" | "assistant";
    content: string;
    timestamp: number;
    streaming?: boolean;
    claims?: ClaimVerification[];
    error?: boolean;
}

export declare type ClaimStatus = "verified" | "unverified" | "uncertain" | "skipped" | "error";

export declare type ClaimType = "factual" | "evasion" | "ignorance" | "citation";

export declare interface ClaimVerification {
    claim: string;
    claim_type?: ClaimType;
    status: ClaimStatus;
    confidence: number;
    similarity?: number;
    reason?: string;
    source_artifact_id?: string;
    source_filename?: string;
    source_domain?: string;
    source_snippet?: string;
    source_urls?: string[];
    verification_method?: string;
    verification_model?: string;
    verification_answer?: string;
    nli_entailment?: number;
    nli_contradiction?: number;
    memory_source?: boolean;
    circular_source?: boolean;
}

/**
 * Derive the three linguistic bands from a ClaimVerification.
 * Mirrors deriveBand() in src/web/src/components/verification/types.ts.
 */
export declare function deriveBand(claim: ClaimVerification): VerificationBand;

declare interface FetchOptions {
    signal?: AbortSignal;
    token?: string;
    timeoutMs?: number;
}

/**
 * POST /sdk/v1/query with one retry on transient errors.
 * Throws ApiError on non-retryable HTTP errors, or the original Error on abort/network failure.
 */
export declare function fetchQuery(host: string, body: SDKQueryRequest, opts?: FetchOptions): Promise<SDKQueryResponse>;

/**
 * Types mirroring the /sdk/v1/query backend contract.
 *
 * Source of truth: src/mcp/app/models/sdk.py::SDKQueryResponse
 *                  src/mcp/app/routers/agents.py::AgentQueryRequest
 *                  src/mcp/core/agents/hallucination/models.py::ClaimVerification
 *
 * Do NOT import from src/web/ — the widget is self-contained.
 */
export declare interface SDKQueryRequest {
    query: string;
    domains?: string[];
    top_k?: number;
    use_reranking?: boolean;
    conversation_messages?: Array<{
        role: string;
        content: string;
    }>;
    response_text?: string;
    model?: string;
    enable_self_rag?: boolean;
    cost_sensitivity?: "low" | "medium" | "high";
    query_scope?: "document" | "domain" | "kb";
}

export declare interface SDKQueryResponse {
    context: string;
    sources: SDKSourceChunk[];
    confidence: number;
    domains_searched: string[];
    total_results: number;
    token_budget_used: number;
    graph_results: number;
    results: SDKSourceChunk[];
    /** Agent answer text (may be present when Self-RAG is enabled). */
    answer?: string;
    /** Streaming tokens array (when streaming is enabled). */
    tokens?: string[];
    /** Per-claim verification from the Self-RAG pipeline. */
    claims?: ClaimVerification[];
}

export declare interface SDKSourceChunk {
    content?: string;
    text?: string;
    score?: number;
    relevance?: number;
    domain?: string;
    filename?: string;
    source?: string;
    metadata?: Record<string, unknown>;
}

/** Count sources for a claim. */
export declare function sourceCount(claim: ClaimVerification): number;

/** The three linguistic bands rendered by ClaimBadge. */
export declare type VerificationBand = "verified" | "partial" | "unverified";

export declare type WidgetTheme = "light" | "dark" | "auto";

export { }
