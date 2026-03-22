"""DynamoDB loader tests using moto mock_aws."""

from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from app import database
from app.config import settings
from app.database import (
    get_learned_duration_cascading,
    record_duration_structured,
)

TABLE_NAME = "test-track-timing-loader"


@pytest.fixture(autouse=True)
def dynamo_env(monkeypatch):
    """Configure settings for DynamoDB backend and reset table cache."""
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


class TestDynamoRecordStructured:
    """Test structured duration recording with multi-level aggregates."""

    def test_writes_all_aggregate_levels(self, dynamo_table):
        """Recording a structured duration updates 4 aggregate keys."""
        record_duration_structured(
            competition_id=26008, session_id=1, event_position=0,
            event_name="Elite Men Sprint Final",
            discipline="sprint_match", duration_minutes=12.0,
            classification="elite", gender="men",
        )
        # Level 1: AGGREGATE#sprint_match
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match"}).get("Item")
        assert item is not None
        assert item["count"] == 1

        # Level 2: AGGREGATE#sprint_match##men
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match##men"}).get("Item")
        assert item is not None

        # Level 3: AGGREGATE#sprint_match#elite
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match#elite"}).get("Item")
        assert item is not None

        # Level 4: AGGREGATE#sprint_match#elite#men
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match#elite#men"}).get("Item")
        assert item is not None

    def test_obs_item_prevents_double_counting(self, dynamo_table):
        """OBS# item prevents duplicate aggregate updates."""
        for _ in range(3):
            record_duration_structured(
                competition_id=26008, session_id=1, event_position=0,
                event_name="Elite Men Sprint Final",
                discipline="sprint_match", duration_minutes=12.0,
                classification="elite", gender="men",
            )
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match"}).get("Item")
        assert item["count"] == 1  # Only counted once

    def test_obs_item_stores_per_heat_duration(self, dynamo_table):
        """OBS# item includes per_heat_duration_minutes when present."""
        record_duration_structured(
            competition_id=26008, session_id=1, event_position=5,
            event_name="Keirin", discipline="keirin", duration_minutes=9.0,
            classification="elite", gender="men",
            per_heat_duration_minutes=4.0,
        )
        obs = dynamo_table.get_item(Key={"pk": "OBS#26008#1#5"}).get("Item")
        assert obs is not None
        assert float(obs["per_heat_duration_minutes"]) == pytest.approx(4.0)


class TestDynamoCascadingFallback:
    """Test cascading fallback queries return most-specific level with >=3 samples."""

    def test_returns_most_specific_level(self, dynamo_table):
        """Level 4 (disc+class+gender) returned when >=3 samples exist."""
        for i in range(3):
            record_duration_structured(
                competition_id=26008, session_id=1, event_position=100 + i,
                event_name="Sprint", discipline="sprint_match",
                duration_minutes=10.0 + i,
                classification="elite", gender="men",
            )
        result = get_learned_duration_cascading("sprint_match", "elite", "men")
        assert result is not None
        assert abs(result - 11.0) < 0.1  # avg(10, 11, 12)

    def test_falls_through_to_broader_level(self, dynamo_table):
        """Falls through to Level 1 when specific levels have <3 samples."""
        # Only 1 record at Level 4
        record_duration_structured(
            competition_id=26008, session_id=1, event_position=200,
            event_name="Sprint", discipline="sprint_match",
            duration_minutes=10.0,
            classification="elite", gender="men",
        )
        # Add 2 more at discipline-only level (different classification)
        for i in range(2):
            record_duration_structured(
                competition_id=26008, session_id=1, event_position=201 + i,
                event_name="Sprint", discipline="sprint_match",
                duration_minutes=12.0 + i,
                classification="junior", gender="men",
            )
        # Level 4 has 1, Level 3 has 1, Level 2 has 3, Level 1 has 3
        result = get_learned_duration_cascading("sprint_match", "elite", "men")
        assert result is not None  # Should find enough at some broader level

    def test_returns_none_with_insufficient_samples(self, dynamo_table):
        """Returns None when no level has >=3 samples."""
        record_duration_structured(
            competition_id=26008, session_id=1, event_position=300,
            event_name="Sprint", discipline="unknown_disc",
            duration_minutes=10.0,
            classification="elite", gender="men",
        )
        result = get_learned_duration_cascading("unknown_disc", "elite", "men")
        assert result is None
