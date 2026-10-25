"""Settings read from environment variables (and dockergpt-memory/.env when present)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    database_url: str = _get(
        "DATABASE_URL", "postgresql+psycopg://postgres:postgrespassword@localhost:5433/dockergpt_memory"
    )
    nudgebee_db_url: str = _get("NUDGEBEE_DB_URL")
    qdrant_url: str = _get("QDRANT_URL", "http://localhost:6333")
    temporal_address: str = _get("TEMPORAL_ADDRESS", "localhost:7233")
    temporal_ui_url: str = _get("TEMPORAL_UI_URL", "http://localhost:8233")
    task_queue: str = _get("TASK_QUEUE", "dockergpt-memory")

    llm_base_url: str = _get("LLM_BASE_URL")
    llm_model: str = _get("LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    llm_api_key: str = _get("LLM_API_KEY", "none")
    embed_base_url: str = _get("EMBED_BASE_URL")
    embed_model: str = _get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    embed_api_key: str = _get("EMBED_API_KEY", "none")
    reasoning_budget: int = int(_get("LLM_REASONING_BUDGET", "3000"))

    confidence_threshold: float = float(_get("CONFIDENCE_THRESHOLD", "0.7"))
    approval_timeout_min: int = int(_get("APPROVAL_TIMEOUT_MIN", "15"))
    health_window_sec: int = int(_get("HEALTH_WINDOW_SEC", "25"))
    demo_root: str = _get("DEMO_ROOT", str(PROJECT_ROOT / "demo"))

    kepler_cli: str = _get("KEPLER_CLI")
    kepler_home: str = _get("KEPLER_HOME", str(Path.home() / ".kepler"))
    kepler_timeout_sec: int = int(_get("KEPLER_TIMEOUT_SEC", "90"))

    slack_webhook_url: str = _get("SLACK_WEBHOOK_URL")
    public_web_url: str = _get("PUBLIC_WEB_URL", "http://localhost:3000")


settings = Settings()
