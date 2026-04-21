Regenerate Python lock files after editing requirements.txt.

Run: `make lock-python`

This executes pip-compile inside Docker to generate `src/mcp/requirements.lock` with hashes. Host macOS can have Xcode issues with pip-compile, so Docker is required.

After regeneration, verify with `make deps-check` and report any issues.
