"""Shared pytest configuration and fixtures."""

import asyncio

import httpx
import pytest

from app.config import settings
from app.database import get_db, init_db
from app.main import app
from app.palmares import init_palmares_db


@pytest.fixture(scope="session", autouse=True)
def test_db(tmp_path_factory):
    """
    Redirect the database to an empty temporary file for the entire test session
    and initialise its schema.

    Prevents learned durations stored in the production timings.db from
    contaminating duration estimates and breaking prediction assertions.
    """
    db_path = str(tmp_path_factory.mktemp("db") / "test.db")
    original_db_path = settings.db_path
    original_dynamodb_table = settings.dynamodb_table
    original_palmares_table = settings.palmares_table
    settings.db_path = db_path
    settings.dynamodb_table = ""  # Force SQLite backend for tests
    settings.palmares_table = ""  # Force SQLite backend for palmares tests
    init_db()
    init_palmares_db()

    # Provide a shared HTTP client on app.state for routes that use Depends(get_http_client)
    app.state.http_client = httpx.AsyncClient(
        base_url=settings.tracktiming_base_url,
        timeout=15.0,
    )

    yield

    asyncio.run(app.state.http_client.aclose())
    settings.db_path = original_db_path
    settings.dynamodb_table = original_dynamodb_table
    settings.palmares_table = original_palmares_table


@pytest.fixture(autouse=True)
def clean_learned_durations():
    """Empty the learned-duration tables before each test so rows written by one
    test (e.g. the loader tests) can't change another test's predictions."""
    with get_db() as conn:
        conn.execute("DELETE FROM event_durations")
        conn.execute("DELETE FROM discipline_overrides")
    yield
