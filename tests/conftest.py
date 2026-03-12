"""Shared pytest configuration and fixtures."""
import pytest

from app.config import settings
from app.database import init_db


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
    settings.db_path = db_path
    settings.dynamodb_table = ""  # Force SQLite backend for tests
    init_db()
    yield
    settings.db_path = original_db_path
    settings.dynamodb_table = original_dynamodb_table
