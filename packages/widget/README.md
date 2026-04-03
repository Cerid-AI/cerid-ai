# @cerid-ai/widget

Embeddable chat widget for [Cerid AI](https://cerid.ai) Knowledge Companion.

Three integration modes: script tag, iframe, or React component.

## Quick Start

### 1. Script Tag (Easiest)

Add a single `<script>` tag to any website. A floating chat bubble appears in the bottom-right corner.

```html
<script
  src="https://your-cerid-server.com/widget.js"
  data-cerid-url="https://your-cerid-server.com"
  data-client-id="my-website"
  defer
></script>
```

**Optional attributes:**

| Attribute | Description | Default |
|-----------|-------------|---------|
| `data-cerid-url` | Cerid MCP server URL (required) | - |
| `data-client-id` | Client identifier (required) | - |
| `data-api-key` | API key for authenticated access | - |
| `data-position` | `bottom-right` or `bottom-left` | `bottom-right` |
| `data-theme` | `light`, `dark`, or `auto` | `auto` |
| `data-title` | Widget header title | `Cerid AI` |
| `data-placeholder` | Input placeholder text | `Ask anything...` |
| `data-initial-message` | Welcome message on first open | - |

You can also set a global config object before the script loads:

```html
<script>
  window.ceridChatConfig = {
    apiUrl: "https://your-cerid-server.com",
    clientId: "my-website",
    theme: "dark",
    title: "Help Center",
    initialMessage: "Hi! How can I help you today?",
  };
</script>
<script src="https://your-cerid-server.com/widget.js" defer></script>
```

### 2. iframe

Embed the widget as a full-page chat interface inside an iframe:

```html
<iframe
  src="https://your-cerid-server.com/widget.html?clientId=my-website"
  width="400"
  height="600"
  frameborder="0"
  allow="clipboard-write"
  style="border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,0.1);"
></iframe>
```

### 3. React Component (npm)

Install the package:

```bash
npm install @cerid-ai/widget
```

Use in your React app:

```tsx
import { CeridBubble } from "@cerid-ai/widget";

function App() {
  return (
    <CeridBubble
      config={{
        apiUrl: "https://your-cerid-server.com",
        clientId: "my-app",
        theme: "auto",
        title: "Knowledge Assistant",
        initialMessage: "Ask me anything about our docs!",
      }}
    />
  );
}
```

Or use the panel component directly (no bubble):

```tsx
import { CeridWidget } from "@cerid-ai/widget";

function ChatPage() {
  return (
    <CeridWidget
      config={{
        apiUrl: "https://your-cerid-server.com",
        clientId: "my-app",
      }}
      fullPage
    />
  );
}
```

## API Client (Headless)

Use the API client directly without any UI:

```ts
import { CeridChatAPI } from "@cerid-ai/widget";

const api = new CeridChatAPI({
  apiUrl: "https://your-cerid-server.com",
  clientId: "my-app",
});

await api.sendMessage(
  "What is RAG?",
  (token, accumulated) => console.log("Token:", token),
  (fullText) => console.log("Done:", fullText),
  (error) => console.error("Error:", error),
);
```

## Configuration

```ts
interface CeridWidgetConfig {
  /** Base URL of the Cerid MCP server (required). */
  apiUrl: string;
  /** Client identifier for rate limiting (required). */
  clientId: string;
  /** API key for authenticated access. */
  apiKey?: string;
  /** Widget position. Default: "bottom-right" */
  position?: "bottom-right" | "bottom-left";
  /** Color theme. Default: "auto" */
  theme?: "light" | "dark" | "auto";
  /** Header title. Default: "Cerid AI" */
  title?: string;
  /** Input placeholder. Default: "Ask anything..." */
  placeholder?: string;
  /** Welcome message shown on first open. */
  initialMessage?: string;
}
```

## Communication Protocol

The widget sends POST requests to `{apiUrl}/agent/query`:

```
POST /agent/query
Content-Type: application/json
Accept: text/event-stream
X-Client-ID: widget-{sessionId}

{"query": "...", "conversation_id": "..."}
```

Responses stream via SSE with `data:` prefixed lines:

```
data: {"type": "token", "content": "Hello"}
data: {"type": "token", "content": " there"}
data: {"type": "done"}
```

## Session Management

- A unique session UUID is generated and stored in `localStorage`
- Message history is persisted in `localStorage` (last 50 messages)
- Conversation ID is sent with each request for context continuity
- Call `api.clearHistory()` to reset

## License

Apache-2.0
