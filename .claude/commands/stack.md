Manage the Cerid AI Docker stack. Accept an argument:

- `status` (default): Run `./scripts/validate-env.sh --quick` to check all services
- `start`: Run `./scripts/start-cerid.sh` to start all 4 service groups
- `build`: Run `./scripts/start-cerid.sh --build` to rebuild images after code changes
- `fix`: Run `./scripts/validate-env.sh --fix` to auto-start missing infrastructure
- `logs <service>`: Run `docker logs --tail 50 <service>` (e.g., `ai-companion-mcp`, `bifrost`, `cerid-web`)

Report the result concisely. If services are down, suggest the appropriate fix command.
