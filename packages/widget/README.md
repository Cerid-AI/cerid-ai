# @cerid/widget

Embeddable verified-chat web component for [Cerid AI](https://cerid.ai) Knowledge Companion.

A single `<cerid-chat>` custom element that any website can drop in via a `<script>` tag and immediately get a verified-response chat widget. Built with vanilla HTMLElement, Shadow DOM, and zero framework dependencies.

---

## Installation

### CDN (script tag)

```html
<script src="https://cdn.cerid.ai/widget@0.1/cerid-widget.js"></script>
<cerid-chat host="https://your-cerid-host.local"></cerid-chat>
```

### npm

```bash
npm install @cerid/widget
```

```ts
import "@cerid/widget"; // auto-registers <cerid-chat>
```

---

## Quick Start

```html
<!DOCTYPE html>
<html>
<head>
  <script src="https://cdn.cerid.ai/widget@0.1/cerid-widget.js"></script>
</head>
<body>
  <cerid-chat
    host="https://your-cerid-host.local"
    token="optional-bearer-token"
    theme="auto"
    placeholder="Ask anything about your knowledge base"
  ></cerid-chat>
</body>
</html>
```

---

## Configuration — observed attributes

| Attribute     | Type                      | Default                  | Required | Description                                                      |
|---------------|---------------------------|--------------------------|----------|------------------------------------------------------------------|
| `host`        | `string` (URL)            | —                        | **Yes**  | Base URL of the Cerid deployment (e.g. `https://cerid.example.com`). CORS must be configured on the server. |
| `token`       | `string`                  | —                        | No       | Bearer token sent as `Authorization: Bearer <token>`.            |
| `placeholder` | `string`                  | `"Ask Cerid anything"`   | No       | Input placeholder text.                                          |
| `theme`       | `"light"` \| `"dark"` \| `"auto"` | `"auto"`        | No       | Color theme. `"auto"` follows `prefers-color-scheme`.            |
| `max-claims`  | `number`                  | `50`                     | No       | Maximum claim badges rendered per assistant message. Cap: 200.   |

---

## Theming — CSS custom properties

Override these on the element from the outer page using `::part()` or by setting them on the host. All defaults are set inside the shadow root.

| Property                          | Default (light)           | Meaning                          |
|-----------------------------------|---------------------------|----------------------------------|
| `--cerid-bg`                      | `#ffffff`                 | Panel background                 |
| `--cerid-bg-secondary`            | `#f4f5f7`                 | Secondary surfaces               |
| `--cerid-fg`                      | `#1a1a2e`                 | Primary text                     |
| `--cerid-fg-muted`                | `#6b7280`                 | Muted/secondary text             |
| `--cerid-border`                  | `#e5e7eb`                 | Border colour                    |
| `--cerid-accent`                  | `#3b82f6`                 | Brand accent (buttons, links)    |
| `--cerid-accent-hover`            | `#2563eb`                 | Accent hover state               |
| `--cerid-accent-fg`               | `#ffffff`                 | Text on accent surfaces          |
| `--cerid-user-bg`                 | `#3b82f6`                 | User message bubble background   |
| `--cerid-user-fg`                 | `#ffffff`                 | User message bubble text         |
| `--cerid-assistant-bg`            | `#f4f5f7`                 | Assistant bubble background      |
| `--cerid-assistant-fg`            | `#1a1a2e`                 | Assistant bubble text            |
| `--cerid-badge-verified-bg`       | `rgba(34,197,94,0.12)`    | Verified badge background        |
| `--cerid-badge-verified-border`   | `rgba(34,197,94,0.4)`     | Verified badge border            |
| `--cerid-badge-verified-fg`       | `#15803d`                 | Verified badge text              |
| `--cerid-badge-partial-bg`        | `rgba(245,158,11,0.12)`   | Partial badge background         |
| `--cerid-badge-partial-border`    | `rgba(245,158,11,0.4)`    | Partial badge border             |
| `--cerid-badge-partial-fg`        | `#92400e`                 | Partial badge text               |
| `--cerid-badge-unverified-bg`     | `rgba(239,68,68,0.12)`    | Unverified badge background      |
| `--cerid-badge-unverified-border` | `rgba(239,68,68,0.4)`     | Unverified badge border          |
| `--cerid-badge-unverified-fg`     | `#991b1b`                 | Unverified badge text            |
| `--cerid-font`                    | system-ui stack           | Font family                      |
| `--cerid-radius`                  | `12px`                    | Panel border radius              |
| `--cerid-shadow`                  | `0 8px 32px …`            | Panel drop shadow                |
| `--cerid-transition`              | `180ms ease`              | Transition timing                |

Dark theme values are applied automatically when `theme="dark"` or via `prefers-color-scheme: dark` with `theme="auto"`.

---

## Verification badges

Each assistant response surfaces per-claim verification badges:

| Band         | Icon       | Color  | Meaning                                               |
|--------------|------------|--------|-------------------------------------------------------|
| `verified`   | CheckCircle | Green | Claim verified with ≥1 KB source                     |
| `partial`    | Minus       | Amber | Uncertain OR verified but no direct source            |
| `unverified` | CircleDot   | Red   | Not supported — no source found                       |

Click any badge to open a detail popover with the claim text, confidence score, and source links.

---

## Browser support

Modern evergreen browsers:
- Chrome 90+
- Firefox 88+
- Safari 15+

Requires: Custom Elements v1, Shadow DOM v1, `fetch`, `AbortSignal`.

---

## Security

The widget makes CORS requests to `host`. **The operator must configure CORS on their Cerid deployment** to allow the embedding page's origin. The `token` attribute is sent as a `Bearer` token in the `Authorization` header — use HTTPS in production.

No cookies, no third-party tracking, no external asset requests. All widget assets are inlined into the single JS file.

---

## API endpoint

The widget POSTs to `${host}/sdk/v1/query`:

```json
POST /sdk/v1/query
Content-Type: application/json
Authorization: Bearer <token>

{
  "query": "What is the capital of France?",
  "conversation_messages": [{ "role": "user", "content": "..." }]
}
```

Response (JSON):

```json
{
  "context": "...",
  "answer": "...",
  "confidence": 0.92,
  "claims": [
    {
      "claim": "Paris is the capital of France.",
      "status": "verified",
      "confidence": 0.98,
      "source_filename": "geography.pdf"
    }
  ]
}
```

---

## License

Apache-2.0 — see [LICENSE](../../LICENSE).
