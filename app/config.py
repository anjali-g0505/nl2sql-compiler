"""Settings loaded from environment variables (populated from .env at the repo root)."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# Real environment variables take precedence over .env (override=False), so the same
# code works locally and inside a container where compose injects the values.
load_dotenv(REPO_ROOT / ".env", override=False)


def _require(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        raise RuntimeError(
            f"Missing required environment variable {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


@dataclass(frozen=True)
class Settings:
    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_database: str
    mysql_pool_size: int
    groq_api_key: str        # empty -> no translator: questions get a 503, DSL still works
    groq_model: str
    groq_fallback_model: str  # used when the main model is rate limited; empty = none


settings = Settings(
    mysql_host=_require("MYSQL_HOST"),
    mysql_port=int(_require("MYSQL_PORT")),
    mysql_user=_require("MYSQL_USER"),
    mysql_password=os.getenv("MYSQL_PASSWORD", ""),
    mysql_database=_require("MYSQL_DATABASE"),
    mysql_pool_size=int(os.getenv("MYSQL_POOL_SIZE", "5")),
    groq_api_key=os.getenv("GROQ_API_KEY", ""),
    groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
    groq_fallback_model=os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b"),
)
