from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str
    model: str = "openai/gpt-5-nano"
    db_path: Path = PROJECT_ROOT / "data" / "gaming_mental_health.sqlite"
    request_timeout_s: float = 30.0
    max_retries: int = 2
    retry_backoff_s: float = 1.0
    log_level: str = "INFO"
    log_file: Path = PROJECT_ROOT / "logs" / "pipeline.jsonl"
    max_result_rows: int = 100


def load_settings():
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required. Set it in a .env file.")

    return Settings(
        openrouter_api_key=api_key,
        model=os.getenv("OPENROUTER_MODEL", "openai/gpt-5-nano"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
