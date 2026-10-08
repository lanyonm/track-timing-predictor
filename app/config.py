from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="")

    tracktiming_base_url: str = "https://tracktiming.live"
    db_path: str = "timings.db"
    refresh_interval_seconds: int = 30
    min_learned_samples: int = 3
    # DynamoDB backend — when set, learning data is stored in DynamoDB instead of SQLite
    dynamodb_table: str = ""
    # Palmares DynamoDB table — when set, palmares data is stored in DynamoDB
    palmares_table: str = ""
    aws_region: str = "us-east-1"
    # Origin for links users copy (e.g. https://ttp.lanyonm.org); empty uses the request host
    public_base_url: str = ""
    # IANA timezone of the venue; upstream schedule times are naive venue-local
    venue_tz: str = "America/Toronto"

    @field_validator("venue_tz")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown timezone {v!r}") from e
        return v


settings = Settings()


def get_settings() -> Settings:
    """FastAPI Depends() provider — returns the module-level singleton."""
    return settings
