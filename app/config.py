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


settings = Settings()


def get_settings() -> Settings:
    """FastAPI Depends() provider — returns the module-level singleton."""
    return settings
