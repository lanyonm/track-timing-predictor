"""Tests for DynamoDB code paths in app/database.py."""
import logging
from decimal import Decimal
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import BotoCoreError, ClientError
from moto import mock_aws

from app.config import settings
from app import database


TABLE_NAME = "test-track-timing"


@pytest.fixture(autouse=True)
def dynamo_env(monkeypatch):
    """Configure settings for DynamoDB backend and reset table cache.

    Overrides the session-scoped conftest test_db fixture which forces SQLite mode.
    """
    monkeypatch.setattr(settings, "dynamodb_table", TABLE_NAME)
    monkeypatch.setattr(settings, "aws_region", "us-east-1")
    database._dynamo_table_cache = None
    yield
    database._dynamo_table_cache = None


@pytest.fixture()
def dynamo_table():
    """Create a moto DynamoDB table and yield the boto3 Table resource."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table.meta.client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
        yield table


def _make_client_error(code="InternalServerError"):
    """Create a botocore ClientError for testing."""
    return ClientError(
        {"Error": {"Code": code, "Message": "test error"}},
        "UpdateItem",
    )


class TestDynamoRecordDuration:
    def test_atomic_accumulation(self, dynamo_table):
        """Two calls should accumulate total_minutes and count via ADD."""
        database._dynamo_record_duration("scratch_race", 12.5)
        database._dynamo_record_duration("scratch_race", 14.0)

        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#scratch_race"})["Item"]
        assert item["total_minutes"] == Decimal("26.5")
        assert item["count"] == 2

    def test_botocore_error_is_logged_not_raised(self, dynamo_table, caplog):
        """BotoCoreError should be swallowed and logged."""
        with patch.object(
            database, "_dynamo_table", side_effect=BotoCoreError()
        ):
            with caplog.at_level(logging.ERROR, logger="app.database"):
                database._dynamo_record_duration("scratch_race", 10.0)
            assert "DynamoDB error recording duration" in caplog.text

    def test_client_error_is_logged_not_raised(self, dynamo_table, caplog):
        """ClientError (e.g. throttling) should be swallowed and logged."""
        with patch.object(
            database, "_dynamo_table", side_effect=_make_client_error()
        ):
            with caplog.at_level(logging.ERROR, logger="app.database"):
                database._dynamo_record_duration("scratch_race", 10.0)
            assert "DynamoDB error recording duration" in caplog.text


class TestDynamoGetLearnedDuration:
    def test_returns_none_below_threshold(self, dynamo_table):
        """Should return None when sample count < min_learned_samples."""
        database._dynamo_record_duration("keirin", 8.0)
        database._dynamo_record_duration("keirin", 10.0)
        assert database._dynamo_get_learned_duration("keirin") is None

    def test_returns_average_at_threshold(self, dynamo_table):
        """Should return the average once we reach min_learned_samples."""
        for dur in [8.0, 10.0, 12.0]:
            database._dynamo_record_duration("keirin", dur)
        avg = database._dynamo_get_learned_duration("keirin")
        assert avg == pytest.approx(10.0)

    def test_override_takes_priority(self, dynamo_table):
        """A manual override should be returned regardless of aggregate data."""
        for dur in [8.0, 10.0, 12.0]:
            database._dynamo_record_duration("keirin", dur)
        dynamo_table.put_item(
            Item={"pk": "OVERRIDE#keirin", "duration_minutes": Decimal("7.5")}
        )
        assert database._dynamo_get_learned_duration("keirin") == pytest.approx(7.5)

    def test_override_without_aggregate(self, dynamo_table):
        """Override should work even when no aggregate data exists."""
        dynamo_table.put_item(
            Item={"pk": "OVERRIDE#keirin", "duration_minutes": Decimal("9.0")}
        )
        assert database._dynamo_get_learned_duration("keirin") == pytest.approx(9.0)

    def test_returns_none_for_unknown_discipline(self, dynamo_table):
        assert database._dynamo_get_learned_duration("nonexistent") is None

    def test_error_returns_none_and_logs(self, dynamo_table, caplog):
        """DynamoDB errors should return None and log."""
        with patch.object(
            database, "_dynamo_table", side_effect=_make_client_error()
        ):
            with caplog.at_level(logging.ERROR, logger="app.database"):
                result = database._dynamo_get_learned_duration("keirin")
            assert result is None
            assert "DynamoDB error reading learned duration" in caplog.text


class TestDynamoGetAllLearnedDurations:
    def test_scan_multiple_disciplines(self, dynamo_table):
        """Should return all aggregated disciplines."""
        for dur in [10.0, 12.0, 14.0]:
            database._dynamo_record_duration("sprint", dur)
        for dur in [20.0, 22.0]:
            database._dynamo_record_duration("points_race", dur)

        result = database._dynamo_get_all_learned_durations()
        assert "sprint" in result
        assert result["sprint"] == pytest.approx((12.0, 3))
        assert "points_race" in result
        assert result["points_race"] == pytest.approx((21.0, 2))

    def test_excludes_override_items(self, dynamo_table):
        """OVERRIDE items should not appear in scan results."""
        database._dynamo_record_duration("sprint", 10.0)
        dynamo_table.put_item(
            Item={"pk": "OVERRIDE#sprint", "duration_minutes": Decimal("5.0")}
        )
        result = database._dynamo_get_all_learned_durations()
        assert result["sprint"] == pytest.approx((10.0, 1))

    def test_empty_table(self, dynamo_table):
        assert database._dynamo_get_all_learned_durations() == {}


class TestGetAllLearnedDurationsErrorHandling:
    def test_dynamo_error_returns_empty_and_logs(self, dynamo_table, caplog):
        """DynamoDB errors in get_all_learned_durations should return {} and log at error."""
        with patch.object(
            database, "_dynamo_table", side_effect=_make_client_error()
        ):
            with caplog.at_level(logging.ERROR, logger="app.database"):
                result = database.get_all_learned_durations()
            assert result == {}
            assert "DynamoDB error reading all learned durations" in caplog.text


class TestBackendDispatch:
    def test_dispatches_to_dynamo_when_configured(self, dynamo_table):
        """Public API should dispatch to DynamoDB and return the correct average."""
        database.record_duration(
            competition_id=1,
            session_id=1,
            event_position=1,
            event_name="Scratch Race",
            discipline="scratch_race",
            duration_minutes=12.0,
        )
        database.record_duration(
            competition_id=1,
            session_id=1,
            event_position=1,
            event_name="Scratch Race",
            discipline="scratch_race",
            duration_minutes=14.0,
        )
        database.record_duration(
            competition_id=1,
            session_id=1,
            event_position=1,
            event_name="Scratch Race",
            discipline="scratch_race",
            duration_minutes=16.0,
        )

        avg = database.get_learned_duration("scratch_race")
        assert avg == pytest.approx(14.0)

    def test_get_all_dispatches_to_dynamo(self, dynamo_table):
        """get_all_learned_durations should dispatch to DynamoDB when configured."""
        for dur in [10.0, 12.0, 14.0]:
            database.record_duration(1, 1, 1, "Sprint", "sprint", dur)
        result = database.get_all_learned_durations()
        assert "sprint" in result
        assert result["sprint"] == pytest.approx((12.0, 3))

    def test_dispatches_to_sqlite_when_not_configured(self, monkeypatch, tmp_path):
        """When dynamodb_table is empty, should use SQLite."""
        monkeypatch.setattr(settings, "dynamodb_table", "")
        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr(settings, "db_path", db_path)
        database.init_db()

        database.record_duration(1, 1, 1, "Sprint", "sprint", 5.0)
        # Only 1 sample, below threshold
        assert database.get_learned_duration("sprint") is None
