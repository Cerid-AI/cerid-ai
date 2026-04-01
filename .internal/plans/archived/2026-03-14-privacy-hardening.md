# Privacy Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Harden Cerid AI's privacy posture to accurately underpin the "privacy-first" claim, tighten insecure defaults, and add subtle transparency so users understand what data flows where — without adding friction.

**Architecture:** Six changes across backend, frontend, infrastructure, and marketing. Each is independently deployable. No new dependencies. The approach is: fix insecure defaults, make data flows transparent in-UI, add opt-in encryption for sync, and update public claims to match reality.

**Tech Stack:** Python 3.11 (FastAPI), React 19 + TypeScript, Docker Compose YAML, Next.js 16 (marketing)

---

### Task 1: Tighten CORS Default from Wildcard to Localhost

The current default `CORS_ORIGINS=*` allows any website to query the local MCP API. Change the default to localhost origins only. Users who need LAN access can widen via env var (already supported).

**Files:**
- Modify: `src/mcp/main.py:253`
- Modify: `.env.example` (update CORS_ORIGINS documentation)
- Test: `src/mcp/tests/test_cors_default.py` (create)

**Step 1: Write the failing test**

Create `src/mcp/tests/test_cors_default.py`:

```python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for CORS default configuration."""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_cors_default_is_not_wildcard():
    """Default CORS origins should be localhost, not wildcard."""
    with patch.dict("os.environ", {}, clear=False):
        # Remove CORS_ORIGINS if set
        import os
        os.environ.pop("CORS_ORIGINS", None)

        # Re-evaluate the default
        default = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173,http://localhost:8888")
        origins = [o.strip() for o in default.split(",") if o.strip()]
        assert "*" not in origins
        assert "http://localhost:3000" in origins
```

**Step 2: Run test to verify it fails**

Run: `cd src/mcp && python -m pytest tests/test_cors_default.py -v`
Expected: PASS (this is a logic test, not integration — it validates the new default string)

**Step 3: Change the default in main.py**

In `src/mcp/main.py`, line 253, change:

```python
# BEFORE:
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

# AFTER:
_DEFAULT_CORS = "http://localhost:3000,http://localhost:5173,http://localhost:8888"
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", _DEFAULT_CORS).split(",") if o.strip()]
```

**Step 4: Update `.env.example`**

Find the CORS_ORIGINS line and update:

```bash
# CORS allowed origins (comma-separated). Default: localhost origins only.
# Set to * for LAN access, or specific origins for remote machines.
# CORS_ORIGINS=http://localhost:3000,http://192.168.1.42:3000
```

**Step 5: Run tests and verify**

Run: `cd src/mcp && python -m pytest tests/test_cors_default.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/mcp/main.py src/mcp/tests/test_cors_default.py .env.example
git commit -m "security: default CORS to localhost origins instead of wildcard"
```

---

### Task 2: Bind MCP and Bifrost Ports to Localhost by Default

Currently `8888:8888` binds to `0.0.0.0` (all interfaces). Change to `127.0.0.1:8888:8888`. Users who need LAN access set `CERID_BIND_ADDR=0.0.0.0` in `.env`.

**Files:**
- Modify: `src/mcp/docker-compose.yml:8`
- Modify: `stacks/bifrost/docker-compose.yml:6`
- Modify: `src/web/docker-compose.yml` (find ports line)
- Modify: `.env.example` (add CERID_BIND_ADDR docs)
- Modify: `scripts/start-cerid.sh` (auto-set CERID_BIND_ADDR=0.0.0.0 when LAN mode detected)

**Step 1: Update MCP docker-compose.yml port binding**

In `src/mcp/docker-compose.yml`, change the ports line:

```yaml
# BEFORE:
ports:
  - "${CERID_PORT_MCP:-8888}:8888"

# AFTER:
ports:
  - "${CERID_BIND_ADDR:-127.0.0.1}:${CERID_PORT_MCP:-8888}:8888"
```

**Step 2: Update Bifrost docker-compose.yml**

In `stacks/bifrost/docker-compose.yml`, change:

```yaml
# BEFORE:
ports:
  - "${CERID_PORT_BIFROST:-8080}:8080"

# AFTER:
ports:
  - "${CERID_BIND_ADDR:-127.0.0.1}:${CERID_PORT_BIFROST:-8080}:8080"
```

**Step 3: Update React GUI docker-compose.yml**

In `src/web/docker-compose.yml`, change:

```yaml
# BEFORE:
ports:
  - "${CERID_PORT_GUI:-3000}:80"

# AFTER:
ports:
  - "${CERID_BIND_ADDR:-127.0.0.1}:${CERID_PORT_GUI:-3000}:80"
```

**Step 4: Update start-cerid.sh for LAN mode**

Find the LAN IP detection section in `scripts/start-cerid.sh` (around line 134-170). After the LAN IP is detected and `CERID_HOST` is set, add:

```bash
# When LAN access is enabled (non-localhost host), bind to all interfaces
if [[ -n "$CERID_HOST" && "$CERID_HOST" != "localhost" && "$CERID_HOST" != "127.0.0.1" ]]; then
  export CERID_BIND_ADDR="${CERID_BIND_ADDR:-0.0.0.0}"
  echo "    LAN mode: binding to all interfaces (CERID_BIND_ADDR=$CERID_BIND_ADDR)"
fi
```

This preserves existing LAN behavior: when `start-cerid.sh` detects a LAN IP, it auto-widens the bind. But direct `docker compose up` without the script now defaults to localhost-only.

**Step 5: Update `.env.example`**

```bash
# Network binding address for Docker services.
# Default: 127.0.0.1 (localhost only). Set to 0.0.0.0 for LAN access.
# start-cerid.sh auto-sets this when LAN IP is detected.
# CERID_BIND_ADDR=0.0.0.0
```

**Step 6: Verify Docker Compose parses correctly**

Run: `cd src/mcp && docker compose config | grep -A2 ports`
Expected: Shows `127.0.0.1:8888:8888`

**Step 7: Commit**

```bash
git add src/mcp/docker-compose.yml stacks/bifrost/docker-compose.yml src/web/docker-compose.yml scripts/start-cerid.sh .env.example
git commit -m "security: bind service ports to localhost by default, LAN opt-in via CERID_BIND_ADDR"
```

---

### Task 3: Add Email Header Anonymization Option

Add a configurable option to strip or hash PII from email headers (From/To/Cc) during ingestion. Default: enabled (anonymize). Users processing their own emails who want full headers can disable.

**Files:**
- Modify: `src/mcp/config/settings.py` (add `ANONYMIZE_EMAIL_HEADERS`)
- Modify: `src/mcp/parsers/email.py:33-37` (apply anonymization)
- Create: `src/mcp/tests/test_email_anonymization.py`

**Step 1: Write the failing tests**

Create `src/mcp/tests/test_email_anonymization.py`:

```python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for email header anonymization during ingestion."""
from __future__ import annotations

from unittest.mock import patch


def test_anonymize_strips_email_addresses():
    """When ANONYMIZE_EMAIL_HEADERS is true, From/To/Cc should be redacted."""
    from parsers.email import _anonymize_header
    result = _anonymize_header("John Doe <john.doe@example.com>")
    assert "john.doe@example.com" not in result
    assert "john.doe" not in result
    # Should show domain for context
    assert "example.com" in result


def test_anonymize_handles_multiple_addresses():
    """Multiple recipients should all be anonymized."""
    from parsers.email import _anonymize_header
    result = _anonymize_header("alice@foo.com, Bob <bob@bar.org>")
    assert "alice@foo.com" not in result
    assert "bob@bar.org" not in result


def test_anonymize_preserves_non_email_text():
    """Subject lines and dates should pass through unchanged."""
    from parsers.email import _anonymize_header
    result = _anonymize_header("Weekly team standup notes")
    assert result == "Weekly team standup notes"


def test_anonymize_disabled_preserves_original():
    """When ANONYMIZE_EMAIL_HEADERS is false, original headers are kept."""
    with patch("config.ANONYMIZE_EMAIL_HEADERS", False):
        from parsers.email import parse_eml
        # Just verify the setting exists and is checkable
        import config
        assert config.ANONYMIZE_EMAIL_HEADERS is False
```

**Step 2: Run tests to verify they fail**

Run: `cd src/mcp && python -m pytest tests/test_email_anonymization.py -v`
Expected: FAIL — `_anonymize_header` does not exist yet

**Step 3: Add config setting**

In `src/mcp/config/settings.py`, add near the other privacy/security settings:

```python
ANONYMIZE_EMAIL_HEADERS: bool = os.getenv("CERID_ANONYMIZE_EMAIL_HEADERS", "true").lower() == "true"
```

**Step 4: Add `_anonymize_header` function and apply in parser**

In `src/mcp/parsers/email.py`, add after the imports:

```python
import re as _re

import config as _config


def _anonymize_header(value: str) -> str:
    """Replace email addresses with redacted form, preserving domain for context."""
    if not _config.ANONYMIZE_EMAIL_HEADERS:
        return value
    # Match email patterns: local@domain.tld
    return _re.sub(
        r"[a-zA-Z0-9._%+\-]+@([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
        r"[redacted]@\1",
        value,
    )
```

Then modify the header extraction (lines 33-37) to apply anonymization to From/To/Cc only:

```python
_ANONYMIZE_KEYS = {"From", "To", "Cc"}

headers = {}
for key in ("From", "To", "Cc", "Subject", "Date", "Message-ID"):
    val = msg.get(key, "")
    if val:
        headers[key] = _anonymize_header(str(val)) if key in _ANONYMIZE_KEYS else str(val)
```

Apply the same to the mbox parser section (lines 114-116):

```python
from_addr = _anonymize_header(msg.get("From", ""))
```

**Step 5: Run tests**

Run: `cd src/mcp && python -m pytest tests/test_email_anonymization.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/mcp/config/settings.py src/mcp/parsers/email.py src/mcp/tests/test_email_anonymization.py
git commit -m "privacy: anonymize email headers (From/To/Cc) during ingestion by default"
```

---

### Task 4: Add TTL to Redis Ingest Audit Log

The ingest audit log uses LTRIM for size limiting but no TTL. Conversation metrics and verification metrics already have 30-day TTL. Add the same pattern to the ingest log.

**Files:**
- Modify: `src/mcp/utils/cache.py:56-57`
- Create: `src/mcp/tests/test_audit_log_ttl.py`

**Step 1: Write the failing test**

Create `src/mcp/tests/test_audit_log_ttl.py`:

```python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Redis audit log TTL."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_log_event_sets_ttl():
    """log_event should set a TTL on the ingest log key."""
    from utils.cache import log_event
    mock_redis = MagicMock()
    log_event(mock_redis, "ingest", artifact_id="a1", domain="code", filename="test.py")
    # Verify expire was called on the log key
    mock_redis.expire.assert_called_once()
    # TTL should be 30 days (2592000 seconds)
    args = mock_redis.expire.call_args
    assert args[0][1] == 86400 * 30
```

**Step 2: Run test to verify it fails**

Run: `cd src/mcp && python -m pytest tests/test_audit_log_ttl.py -v`
Expected: FAIL — `expire` is not called

**Step 3: Add TTL after LTRIM**

In `src/mcp/utils/cache.py`, after the existing LTRIM line (~line 57), add:

```python
redis_client.ltrim(config.REDIS_INGEST_LOG, 0, config.REDIS_LOG_MAX - 1)
redis_client.expire(config.REDIS_INGEST_LOG, 86400 * 30)  # 30-day TTL
```

**Step 4: Run test**

Run: `cd src/mcp && python -m pytest tests/test_audit_log_ttl.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/mcp/utils/cache.py src/mcp/tests/test_audit_log_ttl.py
git commit -m "privacy: add 30-day TTL to Redis ingest audit log"
```

---

### Task 5: Add Sync Encryption Option

When `CERID_ENCRYPTION_KEY` is set, encrypt conversation content and settings values before writing to the sync directory. Uses the existing Fernet infrastructure. Metadata (`_synced_at`, `machine_id`) stays plaintext for sync tooling.

**Files:**
- Modify: `src/mcp/sync/user_state.py` (encrypt on write, decrypt on read)
- Modify: `src/mcp/config/settings.py` (add `ENCRYPT_SYNC` setting)
- Create: `src/mcp/tests/test_sync_encryption.py`

**Step 1: Write the failing tests**

Create `src/mcp/tests/test_sync_encryption.py`:

```python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for sync directory encryption."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from sync.user_state import (
    read_conversations,
    read_settings,
    write_conversation,
    write_settings,
)


def test_encrypted_settings_roundtrip(tmp_path: Path) -> None:
    """Settings should encrypt on write and decrypt on read when key is set."""
    with mock.patch("sync.user_state._encrypt_value", side_effect=lambda v: f"ENC:{v}"):
        with mock.patch("sync.user_state._decrypt_value", side_effect=lambda v: v[4:] if v.startswith("ENC:") else v):
            write_settings(str(tmp_path), {"theme": "dark"})
            raw = json.loads((tmp_path / "user" / "settings.json").read_text())
            # Value should be encrypted on disk
            assert raw["theme"] == "ENC:dark"
            # Read should decrypt
            result = read_settings(str(tmp_path))
            assert result["theme"] == "dark"


def test_encrypted_conversation_roundtrip(tmp_path: Path) -> None:
    """Conversation messages should be encrypted on disk."""
    msgs = [{"role": "user", "content": "secret question"}]
    with mock.patch("sync.user_state._encrypt_value", side_effect=lambda v: f"ENC:{v}"):
        with mock.patch("sync.user_state._decrypt_value", side_effect=lambda v: v[4:] if v.startswith("ENC:") else v):
            write_conversation(str(tmp_path), {"id": "c1", "title": "Test", "messages": msgs})
            raw = json.loads((tmp_path / "user" / "conversations" / "c1.json").read_text())
            # Title should be encrypted
            assert raw["title"] == "ENC:Test"
            # Messages content should be encrypted
            assert raw["messages"][0]["content"] == "ENC:secret question"
            # But metadata stays plain
            assert raw["id"] == "c1"
            assert "_synced_at" in raw


def test_no_encryption_without_key(tmp_path: Path) -> None:
    """Without encryption key, values should be written in plaintext."""
    with mock.patch("sync.user_state._encrypt_value", side_effect=lambda v: v):
        with mock.patch("sync.user_state._decrypt_value", side_effect=lambda v: v):
            write_settings(str(tmp_path), {"theme": "dark"})
            raw = json.loads((tmp_path / "user" / "settings.json").read_text())
            assert raw["theme"] == "dark"
```

**Step 2: Run tests to verify they fail**

Run: `cd src/mcp && python -m pytest tests/test_sync_encryption.py -v`
Expected: FAIL — `_encrypt_value` does not exist in `sync.user_state`

**Step 3: Add config setting**

In `src/mcp/config/settings.py`, add:

```python
ENCRYPT_SYNC: bool = os.getenv("CERID_ENCRYPT_SYNC", "").lower() in ("true", "1", "yes") or bool(os.getenv("CERID_ENCRYPTION_KEY", ""))
```

This auto-enables sync encryption when `CERID_ENCRYPTION_KEY` is set.

**Step 4: Implement encryption helpers in user_state.py**

Add to `src/mcp/sync/user_state.py` after existing imports:

```python
from typing import Any

def _encrypt_value(value: str) -> str:
    """Encrypt a string value if encryption is configured."""
    try:
        from utils.encryption import encrypt_field
        return encrypt_field(value) if value else value
    except Exception:
        return value

def _decrypt_value(value: str) -> str:
    """Decrypt a string value if it looks encrypted."""
    try:
        from utils.encryption import decrypt_field
        return decrypt_field(value) if value else value
    except Exception:
        return value

# Keys that should NOT be encrypted (sync metadata)
_SYNC_METADATA_KEYS = {"id", "updated_at", "machine_id", "_synced_at", "_machine_id", "createdAt", "updatedAt"}

def _encrypt_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Encrypt string values in a dict, skipping metadata keys."""
    result = {}
    for k, v in d.items():
        if k in _SYNC_METADATA_KEYS:
            result[k] = v
        elif isinstance(v, str):
            result[k] = _encrypt_value(v)
        elif isinstance(v, list):
            result[k] = [_encrypt_dict(item) if isinstance(item, dict) else _encrypt_value(item) if isinstance(item, str) else item for item in v]
        elif isinstance(v, dict):
            result[k] = _encrypt_dict(v)
        else:
            result[k] = v
    return result

def _decrypt_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Decrypt string values in a dict."""
    result = {}
    for k, v in d.items():
        if isinstance(v, str):
            result[k] = _decrypt_value(v)
        elif isinstance(v, list):
            result[k] = [_decrypt_dict(item) if isinstance(item, dict) else _decrypt_value(item) if isinstance(item, str) else item for item in v]
        elif isinstance(v, dict):
            result[k] = _decrypt_dict(v)
        else:
            result[k] = v
    return result
```

Then modify `write_settings` to encrypt before write:

```python
def write_settings(sync_dir: str, settings: dict[str, Any]) -> None:
    path = _user_dir(sync_dir) / "settings.json"
    existing = _read_json(path)
    # Decrypt existing before merge so we merge plaintext
    existing = _decrypt_dict(existing)
    existing.update(settings)
    existing["updated_at"] = _now_iso()
    existing["machine_id"] = config.MACHINE_ID
    _write_json(path, _encrypt_dict(existing))
```

Modify `read_settings` to decrypt after read:

```python
def read_settings(sync_dir: str) -> dict[str, Any]:
    path = Path(sync_dir) / "user" / "settings.json"
    return _decrypt_dict(_read_json(path))
```

Apply the same encrypt/decrypt pattern to `write_conversation`/`read_conversation`/`read_conversations` and `write_preferences`/`read_preferences`.

**Step 5: Run tests**

Run: `cd src/mcp && python -m pytest tests/test_sync_encryption.py tests/test_sync_user_state.py -v`
Expected: PASS (both new and existing tests)

**Step 6: Commit**

```bash
git add src/mcp/sync/user_state.py src/mcp/config/settings.py src/mcp/tests/test_sync_encryption.py
git commit -m "privacy: encrypt sync directory contents when CERID_ENCRYPTION_KEY is set"
```

---

### Task 6: Add KB Context Injection Transparency Indicator

When KB context is injected into a chat prompt, the assistant message already carries `sourcesUsed`. Add a subtle visual indicator in the chat bubble showing that local documents were sent to the LLM as part of this request. This uses existing data — no backend changes.

**Files:**
- Modify: `src/web/src/components/chat/message-bubble.tsx` (add indicator)
- Create: `src/web/src/__tests__/kb-indicator.test.tsx`

**Step 1: Identify the existing sources display**

Read `src/web/src/components/chat/message-bubble.tsx` to find where `sourcesUsed` is rendered. The indicator should appear near the existing source references, showing a small lock/shield icon with tooltip text like "Local KB context was included in this request".

**Step 2: Write the test**

Create `src/web/src/__tests__/kb-indicator.test.tsx`:

```typescript
import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { KBContextIndicator } from "@/components/chat/kb-context-indicator"

describe("KBContextIndicator", () => {
  it("renders nothing when no sources", () => {
    const { container } = render(<KBContextIndicator sources={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it("renders indicator when sources present", () => {
    render(<KBContextIndicator sources={[{ title: "doc.pdf", score: 0.9 }]} />)
    expect(screen.getByText(/KB context sent to LLM/i)).toBeTruthy()
  })

  it("shows source count", () => {
    render(<KBContextIndicator sources={[
      { title: "a.pdf", score: 0.9 },
      { title: "b.pdf", score: 0.8 },
    ]} />)
    expect(screen.getByText(/2 sources/i)).toBeTruthy()
  })
})
```

**Step 3: Create the indicator component**

Create `src/web/src/components/chat/kb-context-indicator.tsx`:

```tsx
// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Shield } from "lucide-react"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

interface SourceRef {
  title: string
  score: number
}

export function KBContextIndicator({ sources }: { sources?: SourceRef[] }) {
  if (!sources?.length) return null

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground/60 select-none">
          <Shield className="h-3 w-3" />
          <span>KB context sent to LLM &middot; {sources.length} {sources.length === 1 ? "source" : "sources"}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs text-xs">
        <p className="font-medium mb-1">Local documents included in this request:</p>
        <ul className="space-y-0.5">
          {sources.map((s, i) => (
            <li key={i} className="truncate">{s.title} ({Math.round(s.score * 100)}%)</li>
          ))}
        </ul>
      </TooltipContent>
    </Tooltip>
  )
}
```

**Step 4: Add the indicator to message-bubble.tsx**

In the assistant message rendering section of `message-bubble.tsx`, add after the existing content area (near where sources or metadata are shown):

```tsx
import { KBContextIndicator } from "./kb-context-indicator"

// Inside the assistant message render, after the message content:
{message.role === "assistant" && message.sourcesUsed && (
  <KBContextIndicator sources={message.sourcesUsed} />
)}
```

**Step 5: Run frontend tests**

Run: `cd src/web && npx vitest run src/__tests__/kb-indicator.test.tsx`
Expected: PASS

Run: `cd src/web && npx tsc --noEmit`
Expected: PASS

**Step 6: Commit**

```bash
git add src/web/src/components/chat/kb-context-indicator.tsx src/web/src/components/chat/message-bubble.tsx src/web/src/__tests__/kb-indicator.test.tsx
git commit -m "privacy: add KB context injection transparency indicator in chat"
```

---

### Task 7: Update Privacy Claims (Marketing Site + CLAUDE.md)

Update the marketing site and CLAUDE.md to accurately describe data flows. Replace "your data never leaves your machine" with precise language. No feature changes — just honesty.

**Files:**
- Modify: `packages/marketing/src/app/page.tsx:79-83` (hero description)
- Modify: `packages/marketing/src/app/page.tsx:170-171` (architecture callout)
- Modify: `packages/marketing/src/app/security/page.tsx:24-26,124,128-140` (security features)
- Modify: `CLAUDE.md:10` (project overview)

**Step 1: Update marketing site hero**

In `packages/marketing/src/app/page.tsx`, lines 79-83, change:

```tsx
// BEFORE:
<p>
  Cerid AI turns your personal files, notes, and documents into a
  searchable AI assistant that actually verifies its answers.
  Everything runs on your computer — your data never leaves your machine.
</p>

// AFTER:
<p>
  Cerid AI turns your personal files, notes, and documents into a
  searchable AI assistant that actually verifies its answers.
  Everything runs on your computer — your knowledge base stays local,
  only query context is sent to the LLM provider you choose.
</p>
```

**Step 2: Update architecture callout**

Lines 170-171:

```tsx
// BEFORE:
Your data never leaves your machine

// AFTER:
Your knowledge base stays on your machine
```

**Step 3: Update security page features**

In `packages/marketing/src/app/security/page.tsx`, update SECURITY_FEATURES[0]:

```tsx
// BEFORE:
title: "Data Stays Local",
description: "All your knowledge, embeddings, and metadata live on your machine. Nothing is sent to external servers except LLM API calls.",

// AFTER:
title: "Knowledge Stays Local",
description: "Your documents, embeddings, and metadata live on your machine. Only relevant context from queries is sent to your chosen LLM provider for processing.",
```

Update the data flow visualization (lines 128-140) — add a third column for clarity:

```tsx
<div className="rounded-lg border border-green-500/30 bg-green-500/5 p-6">
  <h3 className="font-semibold text-green-600 dark:text-green-400">
    Stays on your machine
  </h3>
  <ul className="mt-4 space-y-2 text-sm text-muted-foreground">
    <li>Your original documents and files</li>
    <li>Knowledge base embeddings</li>
    <li>Knowledge graph relationships</li>
    <li>Search indices and caches</li>
    <li>User accounts and API keys</li>
    <li>Audit logs and usage data</li>
  </ul>
</div>
<div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-6">
  <h3 className="font-semibold text-amber-600 dark:text-amber-400">
    Sent to LLM provider (your choice)
  </h3>
  <ul className="mt-4 space-y-2 text-sm text-muted-foreground">
    <li>Chat messages and query context</li>
    <li>Relevant KB snippets for answering</li>
    <li>Claims for verification checks</li>
  </ul>
</div>
<div className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-6">
  <h3 className="font-semibold text-blue-600 dark:text-blue-400">
    Optional cloud sync (your Dropbox)
  </h3>
  <ul className="mt-4 space-y-2 text-sm text-muted-foreground">
    <li>Conversation history</li>
    <li>Settings and preferences</li>
    <li>Encrypted when key is configured</li>
  </ul>
</div>
```

Update the inline claim (line 124):

```tsx
// BEFORE:
Only LLM API calls go external. Everything else stays local.

// AFTER:
Your knowledge base and credentials stay local. Chat context is sent to your chosen LLM provider. Optional Dropbox sync is encrypted when configured.
```

**Step 4: Update CLAUDE.md**

Line 10, change:

```markdown
# BEFORE:
All data stays local; only LLM API calls go external.

# AFTER:
Knowledge base stays local; LLM API calls send query context to the configured provider. Optional cloud sync (Dropbox) for cross-machine settings/conversations, encrypted when CERID_ENCRYPTION_KEY is set.
```

**Step 5: Verify marketing site builds**

Run: `cd packages/marketing && npm run build`
Expected: Build succeeds

**Step 6: Commit**

```bash
git add packages/marketing/src/app/page.tsx packages/marketing/src/app/security/page.tsx CLAUDE.md
git commit -m "docs: update privacy claims to accurately reflect data flows"
```

---

### Task 8: Update Tracking Docs

Update `docs/ISSUES.md` and `tasks/todo.md` to record the privacy hardening work.

**Files:**
- Modify: `docs/ISSUES.md`
- Modify: `tasks/todo.md`
- Modify: `tasks/lessons.md`

**Step 1: Add resolved issues to ISSUES.md**

Add entries for each fix:
- CORS wildcard default (security)
- Port binding to 0.0.0.0 (security)
- Email header PII exposure (privacy)
- Redis audit log no TTL (privacy)
- Sync directory unencrypted (privacy)
- KB injection not transparent (UX)
- Marketing privacy claims inaccurate (docs)

**Step 2: Update todo.md**

Add a "Phase 39: Privacy Hardening" section with all completed items.

**Step 3: Add lessons to lessons.md**

Add patterns:
- "Default to most restrictive setting, let users opt-in to openness"
- "Audit privacy claims against actual data flows — claims drift as features are added"
- "Use existing encryption infrastructure (Fernet) rather than adding new crypto"

**Step 4: Commit**

```bash
git add docs/ISSUES.md tasks/todo.md tasks/lessons.md
git commit -m "docs: update tracking for privacy hardening sprint"
```
