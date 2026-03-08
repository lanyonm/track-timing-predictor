import os
from dataclasses import dataclass


@dataclass
class Settings:
    tracktiming_base_url: str = "https://tracktiming.live"
    db_path: str = os.getenv("DB_PATH", "timings.db")
    refresh_interval_seconds: int = 30
    min_learned_samples: int = 3
    # DynamoDB backend — when set, learning data is stored in DynamoDB instead of SQLite
    dynamodb_table: str = os.getenv("DYNAMODB_TABLE", "")
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")


settings = Settings()
