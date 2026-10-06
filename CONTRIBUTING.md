# Contributing

Thank you for your interest in contributing to Track Timing Predictor!

## Reporting issues

Please open an issue with:
- A description of the problem or feature request
- The tracktiming.live event url you were using
- Steps to reproduce (for bugs)
- Saved html for the live page when the issue occurred (helps with emergent parsing issues)

## Development setup

```bash
git clone https://github.com/lanyonm/track-timing-predictor.git
cd track-timing-predictor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
uvicorn app.main:app --reload
```

Python 3.11 is required.

## Making changes

1. Fork the repository and create a branch from `main`
2. Make your changes, with tests (parsing changes need a captured fixture in `tests/fixtures/`)
3. Update any documentation the change affects (`README.md`, `CLAUDE.md`, `plans/hosting-plan.md`, `docs/`)
4. Run `pytest` and test against a live or recent tracktiming.live event
5. Open a pull request with a clear description of what changed and why

## Areas where contributions are especially welcome

- **Duration estimates** — better default values in `app/disciplines.py` based on observed events
- **Discipline detection** — improved keyword matching for edge-case event names
- **UI improvements** — the frontend is intentionally minimal; thoughtful enhancements are welcome
- **Additional event sources** — support for other timing platforms

## Code style

- Follow existing patterns in the codebase
- Keep functions small and focused
- Avoid adding dependencies without a clear reason
