Run the project test suites. Accept an optional argument to scope:

- No argument or `all`: Run both Python and frontend tests
- `python` or `py`: Run only Python tests (in Docker)
- `frontend` or `fe`: Run only frontend tests (vitest)
- A specific file path: Run tests for just that file

**Python tests** must run in Docker because the host macOS lacks chromadb.
The container needs build tools for PyStemmer (C extension):
```
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c "apt-get update -qq && apt-get install -y -qq gcc build-essential libffi-dev > /dev/null 2>&1 && pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v --tb=short"
```

**Frontend tests** run locally:
```
cd src/web && npx vitest run
```

Report results clearly: pass count, fail count, any errors. If tests fail, read the failing test file and the source file it tests to diagnose the issue.
