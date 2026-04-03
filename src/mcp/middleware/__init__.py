"""Middleware — Starlette middleware stack for auth, rate limiting, and observability.

Execution order (LIFO — last added runs first):
  1. RequestIDMiddleware  — Sets X-Request-ID for distributed tracing
  2. TenantContextMiddleware — Propagates tenant_id/user_id contextvars
  3. JWTAuthMiddleware    — JWT Bearer validation (multi-user mode only)
  4. APIKeyMiddleware     — X-API-Key header authentication
  5. RateLimitMiddleware  — Per-client sliding window rate limiting
  6. MetricsMiddleware    — Latency and throughput recording
  7. CORSMiddleware       — Cross-origin response headers
"""
