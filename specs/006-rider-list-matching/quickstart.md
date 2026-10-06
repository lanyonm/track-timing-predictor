# Quickstart: Rider List Fallback Matching

## Run the tests

```bash
source .venv/bin/activate
pytest tests/test_rider_list.py tests/test_parser.py tests/test_rider_matching.py tests/test_main.py
pytest   # full suite; must stay green (SC-003)
```

## Try it locally

```bash
uvicorn app.main:app --reload
```

1. Open `http://localhost:8000/schedule/26037`.
2. Set the racer name to `Brian Abers` (or open `/schedule/26037?r=` + URL-safe Base64 of the name).
3. Expect:
   - an info banner: "Events without start lists matched from the Rider List (M6064: S, TS, TT) …"
   - **Entered**: 60-64 Men Sprint Qualifying, 55-64 Men Team Sprint Qualifying, 60-64 Men 500m Time Trial Final
   - **If advancing**: 60-64 Men Sprint 1/4, 1/2 and Final rides; 55-64 Men Team Sprint Final
   - "Found 13 events … (10 if advancing)", as long as those events still have no start lists.
4. Other names to try: `Walter Fowler` (M90, one TT final) and `Becky Achiler` (W4549, TP + TS).
5. Refresh with the browser or HTMX: the server log shows no second Rider List fetch.

The result depends on live state. As sessions get start lists, those events switch to start-list matching.
