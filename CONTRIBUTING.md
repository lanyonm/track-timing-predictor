# Contributing

Thank you for your interest in contributing to Track Timing Predictor!

## Reporting issues

Please open an issue with:
- A description of the problem or feature request
- The tracktiming.live Event ID you were using (if applicable)
- Steps to reproduce (for bugs)

## Development setup

```bash
git clone https://github.com/lanyonm/track-timing-predictor.git
cd track-timing-predictor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Making changes

1. Fork the repository and create a branch from `main`
2. Make your changes
3. Test against a live or recent tracktiming.live event
4. Open a pull request with a clear description of what changed and why

## Areas where contributions are especially welcome

- **Duration estimates** — better default values in `app/disciplines.py` based on observed events
- **Discipline detection** — improved keyword matching for edge-case event names
- **UI improvements** — the frontend is intentionally minimal; thoughtful enhancements are welcome
- **Additional event sources** — support for other timing platforms

## Code style

- Follow existing patterns in the codebase
- Keep functions small and focused
- Avoid adding dependencies without a clear reason
