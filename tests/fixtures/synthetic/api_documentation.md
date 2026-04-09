---
title: "DataStream API v2.3 - Developer Documentation"
date: 2025-09-01
domain: technology/api-documentation
tags: [api, rest, datastream, documentation, developer, endpoints]
author: DataStream Engineering Team
source_type: technical-documentation
---

# DataStream API v2.3 — Developer Documentation

## Overview

The DataStream API v2.3 provides programmatic access to real-time and historical data streaming, ingestion, and query capabilities. This RESTful API enables developers to create, manage, and consume data streams with sub-second latency at scale. All endpoints are served over HTTPS from `https://api.datastream.io/api/v2/`.

## Authentication

DataStream API v2.3 supports two authentication methods, both required simultaneously for all requests:

### Bearer Token

Include a JWT bearer token in the `Authorization` header:

```
Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...
```

Bearer tokens are obtained through the OAuth 2.0 authorization code flow via the `/auth/token` endpoint. Tokens expire after 3600 seconds (1 hour) and must be refreshed using the `/auth/refresh` endpoint.

### API Key

Include your API key in the `X-API-Key` header:

```
X-API-Key: dsk_live_a1b2c3d4e5f6g7h8i9j0
```

API keys are generated from the DataStream Dashboard under Settings > API Keys. Each organization can have up to 25 active API keys. Keys can be scoped to specific permissions: `read`, `write`, `admin`.

## Rate Limits

| Tier | Requests per Minute | Burst Limit | Concurrent Connections |
|---|---|---|---|
| Free | 100 req/min | 20 req/sec | 5 |
| Professional | 1,000 req/min | 100 req/sec | 50 |
| Enterprise | 10,000 req/min | 1,000 req/sec | 500 |

Rate limit headers are included in every response:

```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 847
X-RateLimit-Reset: 1693526400
```

When rate limits are exceeded, the API returns HTTP `429 Too Many Requests` with a `Retry-After` header indicating the number of seconds to wait.

## Core Endpoints

### Streams

#### `GET /api/v2/streams`

List all streams accessible to the authenticated user.

**Query Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `page` | integer | No | Page number (default: 1) |
| `per_page` | integer | No | Results per page (default: 20, max: 100) |
| `status` | string | No | Filter by status: `active`, `paused`, `archived` |
| `created_after` | ISO 8601 | No | Filter streams created after this timestamp |
| `sort` | string | No | Sort field: `created_at`, `name`, `event_count` |

**Response (200 OK):**

```json
{
  "data": [
    {
      "id": "str_8f3a2b1c",
      "name": "production-events",
      "status": "active",
      "created_at": "2025-06-15T10:30:00Z",
      "event_count": 14523897,
      "retention_days": 90,
      "schema_version": "1.4.0"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 47,
    "total_pages": 3
  }
}
```

#### `POST /api/v2/streams`

Create a new data stream.

**Request Body:**

```json
{
  "name": "user-activity-stream",
  "description": "Real-time user interaction events",
  "retention_days": 30,
  "schema": {
    "type": "object",
    "required": ["event_type", "timestamp"],
    "properties": {
      "event_type": { "type": "string" },
      "timestamp": { "type": "string", "format": "date-time" },
      "user_id": { "type": "string" },
      "payload": { "type": "object" }
    }
  },
  "partitioning_key": "user_id"
}
```

**Response (201 Created):** Returns the full stream object with generated `id`.

### Ingest

#### `POST /api/v2/ingest`

Publish events to a stream. Supports batch ingestion of up to 1,000 events per request. Maximum request body size is 5 MB.

**Request Body:**

```json
{
  "stream_id": "str_8f3a2b1c",
  "events": [
    {
      "event_type": "page_view",
      "timestamp": "2025-09-01T14:22:31.456Z",
      "user_id": "usr_29f8a1",
      "payload": {
        "url": "/dashboard",
        "duration_ms": 3420
      }
    }
  ],
  "options": {
    "deduplication_key": "event_id",
    "compression": "gzip"
  }
}
```

**Response (202 Accepted):**

```json
{
  "accepted": 1,
  "rejected": 0,
  "ingestion_id": "ing_f7e2a9b3",
  "estimated_latency_ms": 120
}
```

### Query

#### `POST /api/v2/query`

Execute analytical queries against stream data using DataStream Query Language (DSQL).

**Request Body:**

```json
{
  "stream_id": "str_8f3a2b1c",
  "dsql": "SELECT event_type, COUNT(*) as cnt FROM events WHERE timestamp >= '2025-09-01' GROUP BY event_type ORDER BY cnt DESC LIMIT 10",
  "timeout_seconds": 30,
  "output_format": "json"
}
```

**Response (200 OK):**

```json
{
  "query_id": "qry_4c1d8e2f",
  "status": "completed",
  "execution_time_ms": 847,
  "rows_scanned": 2341567,
  "results": [
    { "event_type": "page_view", "cnt": 892341 },
    { "event_type": "click", "cnt": 456789 }
  ]
}
```

Supported output formats: `json`, `csv`, `parquet`, `arrow`.

## Error Codes

| Code | Status | Description |
|---|---|---|
| `STREAM_NOT_FOUND` | 404 | The specified stream does not exist |
| `SCHEMA_VALIDATION_ERROR` | 400 | Event payload does not match stream schema |
| `QUERY_TIMEOUT` | 408 | Query exceeded the specified timeout |
| `INSUFFICIENT_PERMISSIONS` | 403 | API key lacks required scope |
| `BATCH_TOO_LARGE` | 413 | Ingest batch exceeds 1,000 events or 5 MB |

## Webhooks

Configure webhooks to receive real-time notifications for stream events via `POST /api/v2/webhooks`. Webhook payloads are signed with HMAC-SHA256 using your webhook secret. Verify signatures by comparing the `X-DataStream-Signature` header with your computed HMAC of the raw request body.

## SDKs

Official SDKs are available for Python (`datastream-python`), Node.js (`@datastream/sdk`), Go (`datastream-go`), and Java (`com.datastream:sdk`). All SDKs support automatic retry with exponential backoff, connection pooling, and batch ingestion.

## Versioning

The API uses URL path versioning. Version v2.3 is the current stable release. Version v1.x is deprecated and will be sunset on March 1, 2026. Breaking changes are communicated 90 days in advance via the DataStream changelog and developer mailing list.
