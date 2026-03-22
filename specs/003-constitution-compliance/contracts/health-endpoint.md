# Contract: Health Endpoint

## Route

`GET /health`

## Response

**HTTP Status**: Always `200 OK`

**Content-Type**: `application/json`

### Healthy state

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

### Degraded state

```json
{
  "status": "degraded",
  "components": {
    "database": {
      "status": "degraded",
      "detail": "<human-readable error description>"
    }
  }
}
```

## Behavior

- Top-level `status` is `"healthy"` if all components are healthy, `"degraded"` if any component is degraded
- Database check adapts to the active backend (SQLite or DynamoDB based on `DYNAMODB_TABLE` setting)
- Total response time MUST be under 5 seconds even if subsystem checks are slow
- Upstream API (tracktiming.live) is NOT checked by this endpoint
- The `detail` field in degraded responses MUST contain a sanitized human-readable summary (e.g., "SQLite connection failed", "DynamoDB table not accessible"), NOT raw exception messages, tracebacks, or internal file paths
