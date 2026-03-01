"""Shared pytest configuration and fixtures."""
import pytest

from app.config import settings


@pytest.fixture(scope="session", autouse=True)
def test_db(tmp_path_factory):
    """
    Redirect the database to an empty temporary file for the entire test session.

    Prevents learned durations stored in the production timings.db from
    contaminating duration estimates and breaking prediction assertions.
    """
    db_path = str(tmp_path_factory.mktemp("db") / "test.db")
    original = settings.db_path
    settings.db_path = db_path
    yield
    settings.db_path = original
