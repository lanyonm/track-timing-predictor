# Quickstart: Constitution Compliance Fixes

## Prerequisites

- Python 3.11+
- Virtual environment activated: `source .venv/bin/activate`

## New dependency

```bash
pip install pydantic-settings
```

This replaces the manual `os.getenv()` pattern with Pydantic's validated settings. Already required by the constitution.

## Removed dependency

`python-multipart` is removed from `requirements.txt`. It was unused (all routes are GET-only).

## Key changes to be aware of

### Configuration (`app/config.py`)

Before: `dataclass` + `os.getenv()`
After: `pydantic_settings.BaseSettings` with automatic env var loading and type validation

Settings instance is now created via `Depends(get_settings)` in route handlers instead of importing the module-level `settings` singleton.

### HTTP Client (`app/fetcher.py`)

Before: Each fetcher function creates `async with httpx.AsyncClient() as client:`
After: A shared `httpx.AsyncClient` is created in the FastAPI lifespan, stored on `app.state.http_client`, and passed to fetcher functions as a parameter.

Pool configuration: `httpx.Limits(max_connections=100, max_keepalive_connections=50)`

### Testing

Tests that need custom settings should use `app.dependency_overrides[get_settings]` or `monkeypatch.setenv()` + fresh `Settings()` construction. The `conftest.py` fixture is updated accordingly.

## Running tests

```bash
pytest
```

All existing tests must continue to pass. New tests added for:
- Health endpoint component status
- Index form redirect route
- Settings validation
