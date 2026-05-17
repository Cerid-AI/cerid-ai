Manage the Cerid AI Docker stack (Grok Build version).

Accept an argument:
- `status` (default): Run `./scripts/validate-env.sh --quick` to check all services
- `start`: Run `./scripts/start-cerid.sh` to start all 4 service groups
- `build`: Run `./scripts/start-cerid.sh --build` to rebuild images after code changes
- `fix`: Run `./scripts/validate-env.sh --fix` to auto-start missing infrastructure
- `logs <service>`: Run `docker logs --tail 50 <service>`

This is the Grok-native equivalent of the Claude `/stack` command.
