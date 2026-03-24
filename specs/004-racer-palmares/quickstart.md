# Quickstart: 004-racer-palmares

## Prerequisites

```bash
source .venv/bin/activate
pip install -r requirements.txt  # No new dependencies
```

## Run the App

```bash
uvicorn app.main:app --reload
# Open http://localhost:8000
```

## Test the Palmares Feature

1. **Set up racer identity**: Enter a competition ID (e.g., 26008), then enter a racer name that appears on a start list (e.g., "Charlie Pittard")
2. **Automatic collection**: View the schedule — matched timed events with audit links are saved automatically. Check the racer info area for "N of your timed events are in your palmares"
3. **View palmares**: Navigate to `/palmares` (link in nav bar or racer info message)
4. **Share**: Copy the shareable URL from the palmares page and open in incognito
5. **CSV export**: Click the download icon next to any event on the palmares page
6. **Remove competition**: Click the remove action on a competition card (only visible when using cookie, not shared links)

## Run Tests

```bash
pytest                                    # All tests
pytest tests/test_palmares.py             # Palmares database tests
pytest tests/test_audit_parser.py         # Audit page parsing tests
pytest tests/test_palmares_routes.py      # Route handler tests
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `timings.db` | SQLite database path (local dev only) |
| `DYNAMODB_TABLE` | `""` | DynamoDB table name for learning data |
| `PALMARES_TABLE` | `""` | DynamoDB table name for palmares data; enables DynamoDB backend when set |
| `AWS_REGION` | `us-east-1` | AWS region for DynamoDB client |

## Key Files

| File | Purpose |
|------|---------|
| `app/palmares.py` | Palmares database operations (dual SQLite/DynamoDB backend) |
| `app/audit_parser.py` | Parse tracktiming.live audit HTML for CSV export |
| `app/templates/palmares.html` | Palmares profile page template |
| `tests/fixtures/audit-pursuit-26008.html` | Captured audit page fixture |
