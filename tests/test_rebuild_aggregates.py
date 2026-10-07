"""Tests for tools.rebuild_aggregates using moto mock_aws."""

from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from app import database
from app.config import settings
from app.database import get_all_learned_durations, record_duration_structured
from tools import rebuild_aggregates
from tools.rebuild_aggregates import apply_changes, plan_changes

TABLE_NAME = "test-track-timing-rebuild"


@pytest.fixture(autouse=True)
def dynamo_env(monkeypatch):
    monkeypatch.setattr(settings, "dynamodb_table", TABLE_NAME)
    monkeypatch.setattr(settings, "aws_region", "us-east-1")
    database._dynamo_table_cache = None
    yield
    database._dynamo_table_cache = None


@pytest.fixture()
def dynamo_table():
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


def _record(pos, discipline, minutes, classification=None, gender=None):
    record_duration_structured(
        competition_id=26008, session_id=1, event_position=pos,
        event_name="x", discipline=discipline, duration_minutes=minutes,
        classification=classification, gender=gender,
    )


def _inflate(table, key, minutes, count):
    """Simulate pre-idempotency live writes: aggregate ADDs with no OBS# item."""
    table.update_item(
        Key={"pk": key},
        UpdateExpression="ADD total_minutes :d, #cnt :c",
        ExpressionAttributeNames={"#cnt": "count"},
        ExpressionAttributeValues={":d": Decimal(str(minutes)), ":c": count},
    )


def _aggregates(table):
    items = table.scan()["Items"]
    return {
        i["pk"]: (i["total_minutes"], i["count"])
        for i in items if i["pk"].startswith("AGGREGATE#")
    }


def _rebuild(table):
    apply_changes(table, plan_changes(table.scan()["Items"]))


class TestRebuild:
    def test_aggregates_equal_obs_sums_at_every_level(self, dynamo_table):
        _record(0, "sprint_match", 12.0, "elite", "men")
        _record(1, "sprint_match", 10.0, "elite", "women")
        _record(2, "sprint_match", 8.5, "junior", None)
        _record(3, "points_race", 20.0)
        expected = _aggregates(dynamo_table)

        _inflate(dynamo_table, "AGGREGATE#sprint_match", 500.0, 40)
        _inflate(dynamo_table, "AGGREGATE#sprint_match#elite#men", 120.0, 10)
        _inflate(dynamo_table, "AGGREGATE#points_race", 19300.0, 1000)
        _inflate(dynamo_table, "AGGREGATE#scratch_race", 60.0, 5)  # no OBS# supports it

        _rebuild(dynamo_table)

        assert _aggregates(dynamo_table) == expected
        assert expected == {
            "AGGREGATE#sprint_match": (Decimal("30.5"), 3),
            "AGGREGATE#sprint_match##men": (Decimal("12.0"), 1),
            "AGGREGATE#sprint_match##women": (Decimal("10.0"), 1),
            "AGGREGATE#sprint_match#elite": (Decimal("22.0"), 2),
            "AGGREGATE#sprint_match#junior": (Decimal("8.5"), 1),
            "AGGREGATE#sprint_match#elite#men": (Decimal("12.0"), 1),
            "AGGREGATE#sprint_match#elite#women": (Decimal("10.0"), 1),
            "AGGREGATE#points_race": (Decimal("20.0"), 1),
        }
        assert get_all_learned_durations()["points_race"] == (20.0, 1)

    def test_overrides_and_obs_items_unchanged(self, dynamo_table):
        _record(0, "keirin", 9.0)
        dynamo_table.put_item(Item={"pk": "OVERRIDE#keirin", "duration_minutes": Decimal("11")})
        _inflate(dynamo_table, "AGGREGATE#keirin", 90.0, 10)
        before = {i["pk"]: i for i in dynamo_table.scan()["Items"] if not i["pk"].startswith("AGGREGATE#")}

        _rebuild(dynamo_table)

        after = {i["pk"]: i for i in dynamo_table.scan()["Items"] if not i["pk"].startswith("AGGREGATE#")}
        assert after == before

    def test_live_obs_without_categories_count_at_level_one(self, dynamo_table):
        dynamo_table.put_item(Item={
            "pk": "OBS#26037#2#4", "discipline": "tempo_race",
            "duration_minutes": Decimal("14.5"), "source": "observed",
        })
        _rebuild(dynamo_table)
        assert _aggregates(dynamo_table) == {"AGGREGATE#tempo_race": (Decimal("14.5"), 1)}

    def test_second_run_finds_nothing(self, dynamo_table):
        _record(0, "keirin", 9.0, "elite", "men")
        _inflate(dynamo_table, "AGGREGATE#keirin", 90.0, 10)
        _rebuild(dynamo_table)
        assert plan_changes(dynamo_table.scan()["Items"]) == []

    def test_dry_run_writes_nothing(self, dynamo_table, monkeypatch, capsys):
        _record(0, "keirin", 9.0)
        _inflate(dynamo_table, "AGGREGATE#keirin", 90.0, 10)
        _inflate(dynamo_table, "AGGREGATE#madison", 30.0, 1)
        before = _aggregates(dynamo_table)

        monkeypatch.setattr("sys.argv", ["rebuild_aggregates"])
        rebuild_aggregates.main()

        assert _aggregates(dynamo_table) == before
        out = capsys.readouterr().out
        assert "update  AGGREGATE#keirin" in out
        assert "delete  AGGREGATE#madison" in out
        assert "Dry run" in out

    def test_apply_flag_writes(self, dynamo_table, monkeypatch):
        _record(0, "keirin", 9.0)
        _inflate(dynamo_table, "AGGREGATE#keirin", 90.0, 10)

        monkeypatch.setattr("sys.argv", ["rebuild_aggregates", "--apply"])
        rebuild_aggregates.main()

        assert _aggregates(dynamo_table) == {"AGGREGATE#keirin": (Decimal("9.0"), 1)}
