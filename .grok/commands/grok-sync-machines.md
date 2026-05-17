# Sync Development Machines (Grok Build)

Compare and synchronize state between this machine (Mac Pro) and the other primary dev machine (Chronos).

**Powered by the `sync-dev-machines` skill.**

**Typical usage:**
- `compare` or no argument → Show drift between the two machines (dotfiles, plugins, MCP servers, installed skills, LaunchAgents, etc.)
- `sync` → Perform idempotent synchronization of dotfiles, Grok/Claude config, age keys, etc.
- `check` → Quick health check that both machines are reasonably in sync

This is extremely useful when you’ve been working on one machine and want to bring the other up to date, or when debugging "why does it work on one machine but not the other?"

**Recommended before major work sessions** if you’ve recently switched machines.
