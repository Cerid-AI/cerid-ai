Run the project test suites. Accept an optional argument to scope:

- No argument or `all`: Run both Python and frontend tests
- `python` or `py`: Run only Python tests (in Docker)
- `frontend` or `fe`: Run only frontend tests (vitest)
- A specific file path: Run tests for just that file

**Python tests** must run in Docker because the host macOS lacks chromadb:
```
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v --tb=short"
```

**Frontend tests** run locally:
```
cd src/web && npx vitest run
```

Report results clearly: pass count, fail count, any errors. If tests fail, read the failing test file and the source file it tests to diagnose the issue.
