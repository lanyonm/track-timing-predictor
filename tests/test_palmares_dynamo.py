"""Tests for palmares DynamoDB backend."""
import boto3
import pytest
from moto import mock_aws

from app.config import settings
from app import palmares
from app.models import PalmaresEntry


PALMARES_TABLE = "test-track-timing-palmares"


@pytest.fixture(autouse=True)
def dynamo_env(monkeypatch):
    """Configure settings for DynamoDB palmares backend and reset table cache."""
    monkeypatch.setattr(settings, "palmares_table", PALMARES_TABLE)
    monkeypatch.setattr(settings, "aws_region", "us-east-1")
    palmares._palmares_table_cache = None
    yield
    palmares._palmares_table_cache = None


@pytest.fixture()
def dynamo_table():
    """Create a mock DynamoDB palmares table."""
    with mock_aws():
        client = boto3.resource("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=PALMARES_TABLE,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


def _make_entry(racer="charlie pittard", comp_id=26008, comp_name="Ontario Track Championships",
                comp_date="2026-02-28", session_id=1, session_name="Friday",
                position=3, event_name="U17 Women Pursuit Final",
                audit_url="results/E26008/W1516-IP-2000-F-0-AUDIT-R.htm"):
    return PalmaresEntry(
        racer_name=racer,
        competition_id=comp_id,
        competition_name=comp_name,
        competition_date=comp_date,
        session_id=session_id,
        session_name=session_name,
        event_position=position,
        event_name=event_name,
        audit_url=audit_url,
    )


class TestDynamoSave:
    def test_save_and_retrieve(self, dynamo_table):
        entries = [
            _make_entry(position=3, event_name="U17 Women Pursuit Final"),
            _make_entry(position=5, event_name="U17 Women Scratch Race"),
        ]
        palmares.save_palmares_entries(entries)
        result = palmares.get_palmares("charlie pittard")
        assert len(result) == 1
        assert result[0].competition_id == 26008
        assert len(result[0].entries) == 2

    def test_idempotent_resave(self, dynamo_table):
        entry = _make_entry(position=10, event_name="Points Race")
        palmares.save_palmares_entries([entry])
        palmares.save_palmares_entries([entry])  # resave
        result = palmares.get_palmares("charlie pittard")
        comp = next(c for c in result if c.competition_id == 26008)
        positions = [e.event_position for e in comp.entries]
        assert positions.count(10) == 1


class TestDynamoCount:
    def test_count(self, dynamo_table):
        entries = [
            _make_entry(racer="count dyn", position=1, event_name="E1"),
            _make_entry(racer="count dyn", position=2, event_name="E2"),
        ]
        palmares.save_palmares_entries(entries)
        assert palmares.count_competition_palmares("count dyn", 26008) == 2

    def test_count_zero(self, dynamo_table):
        assert palmares.count_competition_palmares("nobody", 99999) == 0


class TestDynamoDelete:
    def test_delete_removes_entries(self, dynamo_table):
        entries = [
            _make_entry(racer="del dyn", comp_id=30001, position=1),
            _make_entry(racer="del dyn", comp_id=30001, position=2),
        ]
        palmares.save_palmares_entries(entries)
        deleted = palmares.delete_competition_palmares("del dyn", 30001)
        assert deleted == 2
        assert palmares.count_competition_palmares("del dyn", 30001) == 0

    def test_delete_preserves_other_competitions(self, dynamo_table):
        entries = [
            _make_entry(racer="del dyn2", comp_id=30002, position=1),
            _make_entry(racer="del dyn2", comp_id=30003, position=1),
        ]
        palmares.save_palmares_entries(entries)
        palmares.delete_competition_palmares("del dyn2", 30002)
        result = palmares.get_palmares("del dyn2")
        assert len(result) == 1
        assert result[0].competition_id == 30003


class TestDynamoGetPalmares:
    def test_empty_for_unknown(self, dynamo_table):
        assert palmares.get_palmares("nobody dyn") == []

    def test_multiple_competitions_reverse_chronological(self, dynamo_table):
        entries = [
            _make_entry(racer="multi dyn", comp_id=25022, comp_name="Series 1",
                        comp_date="2026-01-15", position=1),
            _make_entry(racer="multi dyn", comp_id=25023, comp_name="Series 2",
                        comp_date="2026-02-20", position=1),
        ]
        palmares.save_palmares_entries(entries)
        result = palmares.get_palmares("multi dyn")
        assert len(result) == 2
        assert result[0].competition_id == 25023
        assert result[1].competition_id == 25022
