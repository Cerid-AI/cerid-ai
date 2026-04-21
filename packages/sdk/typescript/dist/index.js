// src/errors.ts
var CeridSDKError = class extends Error {
  status;
  body;
  constructor(message, status, body) {
    super(message);
    this.name = "CeridSDKError";
    this.status = status;
    this.body = body;
  }
};
var AuthenticationError = class extends CeridSDKError {
  constructor(message = "Authentication failed", body) {
    super(message, 401, body);
    this.name = "AuthenticationError";
  }
};
var RateLimitError = class extends CeridSDKError {
  constructor(message = "Rate limit exceeded", body) {
    super(message, 429, body);
    this.name = "RateLimitError";
  }
};
var ValidationError = class extends CeridSDKError {
  constructor(message = "Validation error", body) {
    super(message, 422, body);
    this.name = "ValidationError";
  }
};
var NotFoundError = class extends CeridSDKError {
  constructor(message = "Resource not found", body) {
    super(message, 404, body);
    this.name = "NotFoundError";
  }
};
var ServiceUnavailableError = class extends CeridSDKError {
  constructor(message = "Service unavailable", body) {
    super(message, 503, body);
    this.name = "ServiceUnavailableError";
  }
};
async function raiseForStatus(response) {
  if (response.ok) return;
  let body;
  try {
    body = await response.json();
  } catch {
    body = await response.text().catch(() => null);
  }
  const detail = typeof body === "object" && body !== null && "detail" in body ? String(body.detail) : `HTTP ${response.status}`;
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

// src/client.ts
var BaseResource = class {
  constructor(_baseUrl, _headers, _fetch) {
    this._baseUrl = _baseUrl;
    this._headers = _headers;
    this._fetch = _fetch;
  }
  _baseUrl;
  _headers;
  _fetch;
  async _get(path) {
    const response = await this._fetch(`${this._baseUrl}${path}`, {
      method: "GET",
      headers: this._headers
    });
    await raiseForStatus(response);
    return await response.json();
  }
  async _post(path, body) {
    const response = await this._fetch(`${this._baseUrl}${path}`, {
      method: "POST",
      headers: { ...this._headers, "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    await raiseForStatus(response);
    return await response.json();
  }
};
var KBResource = class extends BaseResource {
  /** Multi-domain knowledge base search with hybrid BM25+vector retrieval. */
  async query(params) {
    return this._post("/sdk/v1/query", params);
  }
  /** Direct vector search without agent orchestration. */
  async search(params) {
    return this._post("/sdk/v1/search", params);
  }
  /** Ingest raw text content into the knowledge base. */
  async ingest(params) {
    return this._post("/sdk/v1/ingest", params);
  }
  /** Ingest a file from the archive or an absolute path. */
  async ingestFile(params) {
    return this._post("/sdk/v1/ingest/file", params);
  }
  /** List all knowledge base collections. */
  async collections() {
    return this._get("/sdk/v1/collections");
  }
  /** Get the domain taxonomy tree. */
  async taxonomy() {
    return this._get("/sdk/v1/taxonomy");
  }
};
var VerifyResource = class extends BaseResource {
  /** Verify factual claims in a response against the KB. */
  async check(params) {
    return this._post("/sdk/v1/hallucination", params);
  }
};
var MemoryResource = class extends BaseResource {
  /** Extract memories from conversation text and store as KB artifacts. */
  async extract(params) {
    return this._post("/sdk/v1/memory/extract", params);
  }
};
var SystemResource = class extends BaseResource {
  /** Service health with feature flags. */
  async health() {
    return this._get("/sdk/v1/health");
  }
  /** Extended health check with circuit breaker states and uptime. */
  async healthDetailed() {
    return this._get("/sdk/v1/health/detailed");
  }
  /** Read-only server configuration: version, tier, and feature flags. */
  async settings() {
    return this._get("/sdk/v1/settings");
  }
  /** List all loaded plugins with status and capabilities. */
  async plugins() {
    return this._get("/sdk/v1/plugins");
  }
};
var CeridClient = class {
  kb;
  verify;
  memory;
  system;
  constructor(options) {
    const baseUrl = options.baseUrl.replace(/\/+$/, "");
    const fetchFn = options.fetch ?? globalThis.fetch;
    const headers = {
      "X-Client-ID": options.clientId
    };
    if (options.apiKey) {
      headers["X-API-Key"] = options.apiKey;
    }
    this.kb = new KBResource(baseUrl, headers, fetchFn);
    this.verify = new VerifyResource(baseUrl, headers, fetchFn);
    this.memory = new MemoryResource(baseUrl, headers, fetchFn);
    this.system = new SystemResource(baseUrl, headers, fetchFn);
  }
};
export {
  AuthenticationError,
  CeridClient,
  CeridSDKError,
  KBResource,
  MemoryResource,
  NotFoundError,
  RateLimitError,
  ServiceUnavailableError,
  SystemResource,
  ValidationError,
  VerifyResource,
  raiseForStatus
};
