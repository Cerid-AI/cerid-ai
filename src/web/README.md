# Cerid AI — React GUI

The browser-based interface for Cerid AI. Built with React 19, Vite 7, Tailwind CSS v4, and shadcn/ui.

## Development

```bash
npm install
npm run dev     # http://localhost:3000
npm run build   # production build to dist/
npm test        # run vitest
```

## Architecture

- `src/components/` — UI components (chat, KB, settings, monitoring, workflows)
- `src/hooks/` — Custom React hooks (conversations, WebSocket sync, verification)
- `src/lib/` — API client, types, utilities
- `src/stores/` — State management

See the root [README](../../README.md) for full project documentation.
