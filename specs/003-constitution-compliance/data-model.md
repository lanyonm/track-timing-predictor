# Data Model: Constitution Compliance Fixes

No data model changes. This feature is a structural refactoring of configuration, HTTP client management, dependency injection, and UI degradation behavior. The SQLite schema, DynamoDB table design, and all Pydantic models remain unchanged.

## Configuration Entity (modified, not new)

The `Settings` class changes from a Python `dataclass` to a Pydantic `BaseSettings` subclass. Fields, types, and defaults remain identical:

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `tracktiming_base_url` | `str` | `"https://tracktiming.live"` | (none — hardcoded default) |
| `db_path` | `str` | `"timings.db"` | `DB_PATH` |
| `refresh_interval_seconds` | `int` | `30` | (none — hardcoded default) |
| `min_learned_samples` | `int` | `3` | (none — hardcoded default) |
| `dynamodb_table` | `str` | `""` | `DYNAMODB_TABLE` |
| `aws_region` | `str` | `"us-east-1"` | `AWS_REGION` |

## Health Endpoint Response (new contract)

The `/health` endpoint response changes from `{"status": "ok"}` to a structured per-component report:

```json
{
  "status": "healthy",
  "components": {
    "database": {
      "status": "healthy"
    }
  }
}
```

When degraded:

```json
{
  "status": "degraded",
  "components": {
    "database": {
      "status": "degraded",
      "detail": "Connection failed: ..."
    }
  }
}
```

HTTP status is always 200 (per clarification Q1).
