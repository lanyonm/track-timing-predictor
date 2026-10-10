"""Tests for DynamoDB code paths in app/database.py."""

import logging
from decimal import Decimal
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import BotoCoreError, ClientError
from moto import mock_aws

from app import database
from app.config import settings

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


def _seed(discipline: str, durations: list[float]) -> None:
    """Record one live observation per duration, each for a distinct event."""
    for pos, dur in enumerate(durations):
        database.record_live_duration(1, 1, pos, discipline, discipline, dur, "observed")


def _aggregate(table, discipline: str) -> tuple[Decimal, int]:
    item = table.get_item(Key={"pk": f"AGGREGATE#{discipline}"})["Item"]
    return item["total_minutes"], int(item["count"])


class TestDynamoRecordLiveDuration:
    def test_distinct_events_accumulate(self, dynamo_table):
        _seed("scratch_race", [12.5, 14.0])
        assert _aggregate(dynamo_table, "scratch_race") == (Decimal("26.5"), 2)

    def test_repeat_write_is_idempotent(self, dynamo_table):
        """Cold starts and concurrent containers re-record the same event."""
        for _ in range(3):
            database.record_live_duration(1, 1, 1, "Scratch", "scratch_race", 12.0, "observed")
        assert _aggregate(dynamo_table, "scratch_race") == (Decimal("12.0"), 1)

    def test_observed_replaces_wall_clock(self, dynamo_table):
        database.record_live_duration(1, 1, 1, "Scratch", "scratch_race", 20.0, "wall_clock")
        assert database.record_live_duration(1, 1, 1, "Scratch", "scratch_race", 12.0, "observed") == "updated"
        assert _aggregate(dynamo_table, "scratch_race") == (Decimal("12.0"), 1)
        obs = dynamo_table.get_item(Key={"pk": "OBS#1#1#1"})["Item"]
        assert obs["source"] == "observed"

    def test_wall_clock_does_not_replace_observed(self, dynamo_table):
        database.record_live_duration(1, 1, 1, "Scratch", "scratch_race", 12.0, "observed")
        assert database.record_live_duration(1, 1, 1, "Scratch", "scratch_race", 20.0, "wall_clock") == "unchanged"
        assert _aggregate(dynamo_table, "scratch_race") == (Decimal("12.0"), 1)

    def test_does_not_replace_loader_observation(self, dynamo_table):
        database.record_duration_structured(1, 1, 1, "Elite Men Scratch", "scratch_race", 15.0, "elite", "men")
        assert database.record_live_duration(1, 1, 1, "Scratch", "scratch_race", 12.0, "observed") == "unchanged"
        assert _aggregate(dynamo_table, "scratch_race") == (Decimal("15.0"), 1)

    def test_botocore_error_is_logged_not_raised(self, dynamo_table, caplog):
        with patch.object(database, "_dynamo_table", side_effect=BotoCoreError()):
            with caplog.at_level(logging.ERROR, logger="app.database"):
                assert database.record_live_duration(1, 1, 1, "S", "scratch_race", 10.0, "observed") == "error"
            assert "DynamoDB error recording live duration" in caplog.text

    def test_client_error_is_logged_not_raised(self, dynamo_table, caplog):
        with patch.object(database, "_dynamo_table", side_effect=_make_client_error()):
            with caplog.at_level(logging.ERROR, logger="app.database"):
                assert database.record_live_duration(1, 1, 1, "S", "scratch_race", 10.0, "observed") == "error"
            assert "DynamoDB error recording live duration" in caplog.text


class TestDynamoStructuredConcurrentCreate:
    """Two writers race to create the same OBS#: the loser rolls back its aggregate increments."""

    def test_conditional_check_failure_rolls_back(self, dynamo_table):
        class LosesRace:
            def __getattr__(self, name):
                return getattr(dynamo_table, name)

            def put_item(self, **kwargs):
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                    "PutItem",
                )

        with patch.object(database, "_dynamo_table", return_value=LosesRace()):
            outcome = database.record_duration_structured(
                1, 1, 1, "Elite Men Scratch", "scratch_race", 15.0, "elite", "men"
            )
        assert outcome == "unchanged"
        for key in database._build_aggregate_keys("scratch_race", "elite", "men"):
            item = dynamo_table.get_item(Key={"pk": key})["Item"]
            assert (item["total_minutes"], int(item["count"])) == (Decimal("0"), 0)


class TestDynamoGetLearnedDuration:
    def test_returns_none_below_threshold(self, dynamo_table):
        """Should return None when sample count < min_learned_samples."""
        _seed("keirin", [8.0, 10.0])
        assert database.get_learned_duration("keirin") is None

    def test_returns_average_at_threshold(self, dynamo_table):
        """Should return the average once we reach min_learned_samples."""
        _seed("keirin", [8.0, 10.0, 12.0])
        avg = database.get_learned_duration("keirin")
        assert avg == pytest.approx(10.0)

    def test_override_takes_priority(self, dynamo_table):
        """A manual override should be returned regardless of aggregate data."""
        _seed("keirin", [8.0, 10.0, 12.0])
        dynamo_table.put_item(Item={"pk": "OVERRIDE#keirin", "duration_minutes": Decimal("7.5")})
        assert database.get_learned_duration("keirin") == pytest.approx(7.5)

    def test_override_without_aggregate(self, dynamo_table):
        """Override should work even when no aggregate data exists."""
        dynamo_table.put_item(Item={"pk": "OVERRIDE#keirin", "duration_minutes": Decimal("9.0")})
        assert database.get_learned_duration("keirin") == pytest.approx(9.0)

    def test_returns_none_for_unknown_discipline(self, dynamo_table):
        assert database.get_learned_duration("nonexistent") is None

    def test_client_error_returns_none_and_logs(self, dynamo_table, caplog):
        """ClientError should return None and log."""
        with patch.object(database, "_dynamo_table", side_effect=_make_client_error()):
            with caplog.at_level(logging.ERROR, logger="app.database"):
                result = database.get_learned_duration("keirin")
            assert result is None
            assert "DynamoDB error in cascading fallback" in caplog.text

    def test_botocore_error_returns_none_and_logs(self, dynamo_table, caplog):
        """BotoCoreError should return None and log."""
        with patch.object(database, "_dynamo_table", side_effect=BotoCoreError()):
            with caplog.at_level(logging.ERROR, logger="app.database"):
                result = database.get_learned_duration("keirin")
            assert result is None
            assert "DynamoDB error in cascading fallback" in caplog.text


class TestDynamoGetLearnedDurationsCascading:
    def test_resolves_each_key_at_its_most_specific_level(self, dynamo_table):
        for pos in range(3):
            database.record_live_duration(1, 1, pos, "x", "keirin", 10.0, "observed", "age_55_59", "men")
            database.record_live_duration(1, 2, pos, "x", "keirin", 25.0, "observed", None, "women")
        dynamo_table.put_item(Item={"pk": "OVERRIDE#sprint_match##women", "duration_minutes": Decimal("4.5")})
        keys = [
            ("keirin", "age_55_59", "men"),
            ("keirin", "age_45_49", "women"),
            ("keirin", None, "open"),
            ("sprint_match", "age_45_49", "women"),
            ("points_race", None, "men"),
        ]
        assert database.get_learned_durations_cascading(keys) == {
            ("keirin", "age_55_59", "men"): pytest.approx(10.0),
            ("keirin", "age_45_49", "women"): pytest.approx(25.0),
            ("keirin", None, "open"): pytest.approx(17.5),
            ("sprint_match", "age_45_49", "women"): pytest.approx(4.5),
        }

    def test_one_batch_call_for_all_keys(self, dynamo_table):
        client = dynamo_table.meta.client
        with patch.object(client, "batch_get_item", wraps=client.batch_get_item) as batch:
            with patch.object(database, "_dynamo_table", return_value=dynamo_table):
                database.get_learned_durations_cascading([("keirin", "age_55_59", "men"), ("keirin", None, "women")])
        batch.assert_called_once()

    def test_unprocessed_keys_are_retried(self, dynamo_table):
        _seed("keirin", [8.0, 10.0, 12.0])
        client = dynamo_table.meta.client
        real = client.batch_get_item
        calls = []

        def first_call_unprocessed(RequestItems):
            calls.append(RequestItems)
            if len(calls) == 1:
                return {"Responses": {TABLE_NAME: []}, "UnprocessedKeys": RequestItems}
            return real(RequestItems=RequestItems)

        with patch.object(client, "batch_get_item", side_effect=first_call_unprocessed):
            with patch.object(database, "_dynamo_table", return_value=dynamo_table):
                assert database.get_learned_durations_cascading([("keirin", None, None)]) == {
                    ("keirin", None, None): pytest.approx(10.0)
                }
        assert len(calls) == 2

    def test_error_returns_empty_and_logs(self, dynamo_table, caplog):
        with patch.object(database, "_dynamo_table", side_effect=_make_client_error()):
            with caplog.at_level(logging.ERROR, logger="app.database"):
                assert database.get_learned_durations_cascading([("keirin", None, "men")]) == {}
        assert "DynamoDB error in cascading fallback" in caplog.text

    def test_live_write_feeds_finer_aggregates(self, dynamo_table):
        database.record_live_duration(1, 1, 1, "x", "keirin", 10.0, "observed", "age_55_59", "men")
        for pk in ("AGGREGATE#keirin##men", "AGGREGATE#keirin#age_55_59", "AGGREGATE#keirin#age_55_59#men"):
            assert int(dynamo_table.get_item(Key={"pk": pk})["Item"]["count"]) == 1


class TestDynamoGetAllLearnedDurations:
    def test_scan_multiple_disciplines(self, dynamo_table):
        """Should return all aggregated disciplines."""
        _seed("sprint", [10.0, 12.0, 14.0])
        for pos, dur in enumerate([20.0, 22.0]):
            database.record_live_duration(1, 2, pos, "Points", "points_race", dur, "observed")

        result = database._dynamo_get_all_learned_durations()
        assert "sprint" in result
        assert result["sprint"] == pytest.approx((12.0, 3))
        assert "points_race" in result
        assert result["points_race"] == pytest.approx((21.0, 2))

    def test_excludes_override_items(self, dynamo_table):
        """OVERRIDE items should not appear in scan results."""
        _seed("sprint", [10.0])
        dynamo_table.put_item(Item={"pk": "OVERRIDE#sprint", "duration_minutes": Decimal("5.0")})
        result = database._dynamo_get_all_learned_durations()
        assert result["sprint"] == pytest.approx((10.0, 1))

    def test_empty_table(self, dynamo_table):
        assert database._dynamo_get_all_learned_durations() == {}


class TestFinerLearnedDurations:
    def test_aggregate_keys_round_trip(self):
        for key in [("keirin", None, None), ("keirin", None, "men"), ("keirin", "age_55_59", None)] + [
            ("keirin", "age_55_59", "men")
        ]:
            built = database._build_aggregate_keys(*key)[-1]
            assert database._parse_aggregate_key(built) == key

    def test_lists_usable_finer_levels(self, dynamo_table):
        for pos in range(3):
            database.record_live_duration(1, 1, pos, "x", "keirin", 10.0, "observed", "age_55_59", "men")
        database.record_live_duration(1, 2, 0, "x", "keirin", 10.0, "observed", "age_60_64", "men")
        assert database.get_finer_learned_durations() == {
            ("keirin", None, "men"): (pytest.approx(10.0), 4),
            ("keirin", "age_55_59", None): (pytest.approx(10.0), 3),
            ("keirin", "age_55_59", "men"): (pytest.approx(10.0), 3),
        }
        assert database.get_all_learned_durations() == {"keirin": (pytest.approx(10.0), 4)}


class TestGetAllLearnedDurationsErrorHandling:
    def test_client_error_returns_empty_and_logs(self, dynamo_table, caplog):
        """ClientError in _dynamo_get_all_learned_durations should return {} and log."""
        with patch.object(database, "_dynamo_table", side_effect=_make_client_error()):
            with caplog.at_level(logging.ERROR, logger="app.database"):
                result = database._dynamo_get_all_learned_durations()
            assert result == {}
            assert "DynamoDB error reading all learned durations" in caplog.text

    def test_botocore_error_returns_empty_and_logs(self, dynamo_table, caplog):
        """BotoCoreError in _dynamo_get_all_learned_durations should return {} and log."""
        with patch.object(database, "_dynamo_table", side_effect=BotoCoreError()):
            with caplog.at_level(logging.ERROR, logger="app.database"):
                result = database._dynamo_get_all_learned_durations()
            assert result == {}
            assert "DynamoDB error reading all learned durations" in caplog.text


class TestBackendDispatch:
    def test_dispatches_to_dynamo_when_configured(self, dynamo_table):
        """Public API should dispatch to DynamoDB and return the correct average."""
        _seed("scratch_race", [12.0, 14.0, 16.0])
        assert database.get_learned_duration("scratch_race") == pytest.approx(14.0)

    def test_get_all_dispatches_to_dynamo(self, dynamo_table):
        """get_all_learned_durations should dispatch to DynamoDB when configured."""
        _seed("sprint", [10.0, 12.0, 14.0])
        assert database.get_all_learned_durations()["sprint"] == pytest.approx((12.0, 3))

    def test_dispatches_to_sqlite_when_not_configured(self, monkeypatch, tmp_path):
        """When dynamodb_table is empty, should use SQLite."""
        monkeypatch.setattr(settings, "dynamodb_table", "")
        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr(settings, "db_path", db_path)
        database.init_db()

        database.record_live_duration(1, 1, 1, "Sprint", "sprint", 5.0, "observed")
        # Only 1 sample, below threshold
        assert database.get_learned_duration("sprint") is None
