"""DynamoDB loader tests using moto mock_aws."""

from decimal import Decimal
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import BotoCoreError
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


class TestDynamoReloadCorrection:
    """Test idempotent re-load with corrected data."""

    def test_reload_identical_returns_unchanged(self, dynamo_table):
        """Recording identical data twice returns 'unchanged' and aggregates stay at count=1."""
        result1 = record_duration_structured(
            competition_id=26008, session_id=1, event_position=0,
            event_name="Sprint Final", discipline="sprint_match",
            duration_minutes=12.0, classification="elite", gender="men",
        )
        assert result1 == "created"

        result2 = record_duration_structured(
            competition_id=26008, session_id=1, event_position=0,
            event_name="Sprint Final", discipline="sprint_match",
            duration_minutes=12.0, classification="elite", gender="men",
        )
        assert result2 == "unchanged"

        # Aggregates must still show count=1
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match"}).get("Item")
        assert item["count"] == 1
        assert float(item["total_minutes"]) == pytest.approx(12.0)

    def test_reload_corrected_duration(self, dynamo_table):
        """Re-recording with a different duration corrects all shared aggregates."""
        record_duration_structured(
            competition_id=26008, session_id=1, event_position=0,
            event_name="Sprint Final", discipline="sprint_match",
            duration_minutes=12.0, classification="elite", gender="men",
        )

        result = record_duration_structured(
            competition_id=26008, session_id=1, event_position=0,
            event_name="Sprint Final", discipline="sprint_match",
            duration_minutes=14.0, classification="elite", gender="men",
        )
        assert result == "updated"

        # All 4 aggregate levels should have count=1, total=14.0
        for agg_key in [
            "AGGREGATE#sprint_match",
            "AGGREGATE#sprint_match##men",
            "AGGREGATE#sprint_match#elite",
            "AGGREGATE#sprint_match#elite#men",
        ]:
            item = dynamo_table.get_item(Key={"pk": agg_key}).get("Item")
            assert item is not None, f"Missing {agg_key}"
            assert item["count"] == 1, f"Wrong count for {agg_key}"
            assert float(item["total_minutes"]) == pytest.approx(14.0), f"Wrong total for {agg_key}"

        # OBS# item should have new duration
        obs = dynamo_table.get_item(Key={"pk": "OBS#26008#1#0"}).get("Item")
        assert float(obs["duration_minutes"]) == pytest.approx(14.0)

    def test_reload_corrected_category(self, dynamo_table):
        """Re-recording with different classification fixes old and new aggregate keys."""
        record_duration_structured(
            competition_id=26008, session_id=1, event_position=0,
            event_name="Sprint Final", discipline="sprint_match",
            duration_minutes=12.0, classification="elite", gender="men",
        )

        result = record_duration_structured(
            competition_id=26008, session_id=1, event_position=0,
            event_name="Sprint Final", discipline="sprint_match",
            duration_minutes=12.0, classification="junior", gender="men",
        )
        assert result == "updated"

        # Level 1 (shared): count=1, total=12.0 (unchanged)
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match"}).get("Item")
        assert item["count"] == 1
        assert float(item["total_minutes"]) == pytest.approx(12.0)

        # Level 2 disc+gender (shared): count=1, total=12.0
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match##men"}).get("Item")
        assert item["count"] == 1
        assert float(item["total_minutes"]) == pytest.approx(12.0)

        # Old Level 3 (removed): count=0
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match#elite"}).get("Item")
        assert item["count"] == 0

        # New Level 3 (added): count=1, total=12.0
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match#junior"}).get("Item")
        assert item["count"] == 1
        assert float(item["total_minutes"]) == pytest.approx(12.0)

        # Old Level 4 (removed): count=0
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match#elite#men"}).get("Item")
        assert item["count"] == 0

        # New Level 4 (added): count=1, total=12.0
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match#junior#men"}).get("Item")
        assert item["count"] == 1
        assert float(item["total_minutes"]) == pytest.approx(12.0)

    def test_reload_duration_and_category(self, dynamo_table):
        """Changing both duration and classification applies both corrections."""
        record_duration_structured(
            competition_id=26008, session_id=1, event_position=0,
            event_name="Sprint Final", discipline="sprint_match",
            duration_minutes=12.0, classification="elite", gender="men",
        )

        result = record_duration_structured(
            competition_id=26008, session_id=1, event_position=0,
            event_name="Sprint Final", discipline="sprint_match",
            duration_minutes=14.0, classification="junior", gender="men",
        )
        assert result == "updated"

        # Level 1 (shared): count=1, total corrected 12→14
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match"}).get("Item")
        assert item["count"] == 1
        assert float(item["total_minutes"]) == pytest.approx(14.0)

        # Level 2 disc+gender (shared): count=1, total corrected 12→14
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match##men"}).get("Item")
        assert item["count"] == 1
        assert float(item["total_minutes"]) == pytest.approx(14.0)

        # Old Level 3 (removed): decremented
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match#elite"}).get("Item")
        assert item["count"] == 0

        # New Level 3 (added): count=1, total=14.0
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match#junior"}).get("Item")
        assert item["count"] == 1
        assert float(item["total_minutes"]) == pytest.approx(14.0)

    def test_reload_old_obs_without_optional_fields(self, dynamo_table):
        """OBS# items without classification/gender get corrected when re-loaded with them."""
        # Manually insert an OBS# item without optional fields (simulates old data)
        dynamo_table.put_item(Item={
            "pk": "OBS#26008#1#0",
            "discipline": "sprint_match",
            "duration_minutes": Decimal("12.0"),
        })
        # Manually insert Level 1 aggregate
        dynamo_table.update_item(
            Key={"pk": "AGGREGATE#sprint_match"},
            UpdateExpression="ADD total_minutes :d, #cnt :one",
            ExpressionAttributeNames={"#cnt": "count"},
            ExpressionAttributeValues={":d": Decimal("12.0"), ":one": 1},
        )

        # Re-load with classification and gender
        result = record_duration_structured(
            competition_id=26008, session_id=1, event_position=0,
            event_name="Sprint Final", discipline="sprint_match",
            duration_minutes=12.0, classification="elite", gender="men",
        )
        assert result == "updated"

        # Level 1 (shared): count=1, total=12.0 (unchanged)
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match"}).get("Item")
        assert item["count"] == 1
        assert float(item["total_minutes"]) == pytest.approx(12.0)

        # New Level 2, 3, 4 created with count=1
        for agg_key in [
            "AGGREGATE#sprint_match##men",
            "AGGREGATE#sprint_match#elite",
            "AGGREGATE#sprint_match#elite#men",
        ]:
            item = dynamo_table.get_item(Key={"pk": agg_key}).get("Item")
            assert item is not None, f"Missing {agg_key}"
            assert item["count"] == 1
            assert float(item["total_minutes"]) == pytest.approx(12.0)

    def test_first_load_returns_created(self, dynamo_table):
        """First load of a new record returns 'created'."""
        result = record_duration_structured(
            competition_id=26008, session_id=1, event_position=0,
            event_name="Sprint Final", discipline="sprint_match",
            duration_minutes=12.0, classification="elite", gender="men",
        )
        assert result == "created"


class TestDynamoErrorPaths:
    """Test DynamoDB error handling paths."""

    def test_boto_error_returns_error(self, dynamo_table):
        """BotoCoreError during structured write returns 'error'."""
        with patch.object(database, "_dynamo_table") as mock_table_fn:
            mock_table_fn.return_value.get_item.side_effect = BotoCoreError()
            result = record_duration_structured(
                competition_id=26008, session_id=1, event_position=0,
                event_name="Sprint Final", discipline="sprint_match",
                duration_minutes=12.0, classification="elite", gender="men",
            )
        assert result == "error"

    def test_concurrent_write_rolls_back_aggregates(self, dynamo_table):
        """ConditionalCheckFailedException rolls back aggregate increments."""
        # First, write a record normally
        record_duration_structured(
            competition_id=26008, session_id=1, event_position=0,
            event_name="Sprint Final", discipline="sprint_match",
            duration_minutes=12.0, classification="elite", gender="men",
        )
        # Verify count=1
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match"}).get("Item")
        assert item["count"] == 1

        # Now simulate a concurrent write: the OBS# already exists, so the
        # conditional put will fail and aggregates should be rolled back.
        # Write a second record at a different position — but manually
        # insert the OBS# first to trigger ConditionalCheckFailedException
        dynamo_table.put_item(Item={
            "pk": "OBS#26008#1#99",
            "discipline": "sprint_match",
            "duration_minutes": Decimal("12.0"),
            "classification": "elite",
            "gender": "men",
        })
        result = record_duration_structured(
            competition_id=26008, session_id=1, event_position=99,
            event_name="Sprint Final", discipline="sprint_match",
            duration_minutes=12.0, classification="elite", gender="men",
        )
        # Should return unchanged (Branch 2: identical data)
        assert result == "unchanged"
        # Aggregate should still be count=1 (not double-counted)
        item = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match"}).get("Item")
        assert item["count"] == 1


class TestDynamoOverrideLevels:
    """Test that overrides work at all specificity levels."""

    def test_level_4_override(self, dynamo_table):
        """Override at discipline+classification+gender takes priority."""
        # Insert 3 records to get an aggregate
        for i in range(3):
            record_duration_structured(
                competition_id=26008, session_id=1, event_position=400 + i,
                event_name="Sprint", discipline="sprint_match",
                duration_minutes=10.0 + i,
                classification="elite", gender="men",
            )
        # Insert Level 4 override
        dynamo_table.put_item(Item={
            "pk": "OVERRIDE#sprint_match#elite#men",
            "duration_minutes": Decimal("99.0"),
        })
        result = get_learned_duration_cascading("sprint_match", "elite", "men")
        assert result == 99.0

    def test_level_2_override(self, dynamo_table):
        """Override at discipline+gender takes priority over aggregates."""
        for i in range(3):
            record_duration_structured(
                competition_id=26008, session_id=1, event_position=410 + i,
                event_name="Sprint", discipline="keirin",
                duration_minutes=10.0 + i,
                classification="elite", gender="women",
            )
        # Insert Level 2 override (disc##gender)
        dynamo_table.put_item(Item={
            "pk": "OVERRIDE#keirin##women",
            "duration_minutes": Decimal("88.0"),
        })
        result = get_learned_duration_cascading("keirin", "elite", "women")
        assert result == 88.0


class TestDynamoDisciplineChange:
    """Test correction path when discipline changes on re-load."""

    def test_discipline_change_swaps_all_aggregates(self, dynamo_table):
        """Changing discipline removes old aggregates and creates new ones."""
        # Initial load as sprint_match
        record_duration_structured(
            competition_id=26008, session_id=1, event_position=500,
            event_name="Sprint Final", discipline="sprint_match",
            duration_minutes=12.0, classification="elite", gender="men",
        )
        old_agg = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match"}).get("Item")
        assert old_agg is not None
        assert old_agg["count"] == 1

        # Re-load as sprint_qualifying (discipline change)
        result = record_duration_structured(
            competition_id=26008, session_id=1, event_position=500,
            event_name="Sprint Qualifying", discipline="sprint_qualifying",
            duration_minutes=10.0, classification="elite", gender="men",
        )
        assert result == "updated"

        # Old discipline aggregates should be decremented to 0
        old_agg = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_match"}).get("Item")
        assert old_agg["count"] == 0

        # New discipline aggregates should have count=1
        new_agg = dynamo_table.get_item(Key={"pk": "AGGREGATE#sprint_qualifying"}).get("Item")
        assert new_agg is not None
        assert new_agg["count"] == 1
        assert float(new_agg["total_minutes"]) == pytest.approx(10.0)


class TestBuildAggregateKeys:
    """Test _build_aggregate_keys with various None combinations."""

    def test_none_classification_none_gender(self, dynamo_table):
        """With both None, only Level 1 key is generated."""
        from app.database import _build_aggregate_keys
        keys = _build_aggregate_keys("keirin", None, None)
        assert keys == ["AGGREGATE#keirin"]

    def test_none_classification_with_gender(self, dynamo_table):
        """With gender but no classification, Levels 1 and 2 are generated."""
        from app.database import _build_aggregate_keys
        keys = _build_aggregate_keys("keirin", None, "women")
        assert keys == ["AGGREGATE#keirin", "AGGREGATE#keirin##women"]

    def test_with_classification_none_gender(self, dynamo_table):
        """With classification but no gender, Levels 1 and 3 are generated."""
        from app.database import _build_aggregate_keys
        keys = _build_aggregate_keys("keirin", "elite", None)
        assert keys == ["AGGREGATE#keirin", "AGGREGATE#keirin#elite"]

    def test_all_fields_present(self, dynamo_table):
        """With all fields, all 4 levels are generated."""
        from app.database import _build_aggregate_keys
        keys = _build_aggregate_keys("keirin", "elite", "men")
        assert len(keys) == 4
        assert keys[0] == "AGGREGATE#keirin"
        assert keys[1] == "AGGREGATE#keirin##men"
        assert keys[2] == "AGGREGATE#keirin#elite"
        assert keys[3] == "AGGREGATE#keirin#elite#men"

    def test_empty_string_treated_as_falsy(self, dynamo_table):
        """Empty strings are falsy, so they behave like None for key generation."""
        from app.database import _build_aggregate_keys
        keys = _build_aggregate_keys("keirin", "", "")
        assert keys == ["AGGREGATE#keirin"]
