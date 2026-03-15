# Quickstart: Duration Data Import Scripts

**Phase 1 output** | **Date**: 2026-03-15

## Prerequisites

- Python 3.11+ with the project's virtual environment activated
- Project dependencies installed: `pip install -r requirements.txt`
- No additional dependencies required

## Extract a Competition

```bash
# Activate virtual environment
source .venv/bin/activate

# Extract data for a single competition
python -m tools.extract_competition 26008

# Output: data/competitions/26008.json
```

The extraction script:
1. Fetches schedule, result pages, and start-list pages from tracktiming.live
2. Decomposes each event name into structured categories (discipline, classification, gender, round, etc.)
3. Extracts durations using the same priority as the app (finish time > generated timestamps > heat count)
4. Writes a JSON report with duration observations and an uncategorized event summary

## Inspect the Output

```bash
# View the JSON report (human-readable)
python -m json.tool data/competitions/26008.json | head -50

# Check the uncategorized summary for events that need attention
python -c "
import json
with open('data/competitions/26008.json') as f:
    report = json.load(f)
for entry in report.get('uncategorized_summary', []):
    print(f\"{entry['event_name']} -> {entry['unresolved_text']}\")
"
```

## Load into the Learning Database

```bash
# Load into local SQLite database (default)
python -m tools.load_durations data/competitions/26008.json

# Load into DynamoDB (when configured)
DYNAMODB_TABLE=track-timing-durations python -m tools.load_durations data/competitions/26008.json
```

The loader:
- Reads duration observations from JSON reports
- Writes to the app's learning database (SQLite or DynamoDB based on `DYNAMODB_TABLE` env var)
- Handles duplicates idempotently via natural key `(competition_id, session_id, event_position)`
- Reports unrecognized disciplines with warnings

## Batch Processing

```bash
# Extract multiple competitions via shell loop
for id in 25022 25026 25027 25028 25031 26001 26002 26008 26009; do
    python -m tools.extract_competition "$id"
done

# Load all extracted reports
python -m tools.load_durations data/competitions/*.json
```

## Verify Results

```bash
# Run the app and check the /learned page
uvicorn app.main:app --reload
# Open http://localhost:8000/learned to see imported averages

# Or query directly
python -c "
from app.database import init_db, get_all_learned_durations
init_db()
for disc, (avg, count) in sorted(get_all_learned_durations().items()):
    print(f'{disc}: {avg:.1f} min ({count} samples)')
"
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `timings.db` | SQLite database path (used by loader in local mode) |
| `DYNAMODB_TABLE` | `""` | DynamoDB table name; enables DynamoDB backend when set |
| `AWS_REGION` | `us-east-1` | AWS region for DynamoDB client |

## Running Tests

```bash
# All tests including new categorizer and loader tests
pytest

# Categorizer tests only
pytest tests/test_categorizer.py

# Loader tests only
pytest tests/test_loader.py
```
