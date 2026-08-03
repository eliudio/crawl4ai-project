"""
Environment-driven configuration for the crawl pipeline.

Everything here is read from the environment so the exact same container
image runs unchanged on a laptop (via .env / python-dotenv), in a local
Docker Compose setup, or on Cloud Run (via env vars set at deploy time).
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Storage ---
    # Postgres (Cloud SQL in production). Example:
    # postgresql+psycopg://user:password@127.0.0.1:5432/events
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/events"

    # --- Firecrawl ---
    firecrawl_api_key: str | None = None
    # Only set this for a self-hosted Firecrawl instance. Leave unset to use
    # Firecrawl's hosted cloud API, which is what handles proxy rotation /
    # anti-bot for us instead of a local VPN.
    firecrawl_api_url: str | None = None

    # --- LLM extraction (pluggable) ---
    llm_provider: str = "grok"  # "grok" or "anthropic"
    grok_api_key: str | None = None
    grok_model: str = "grok-4-1-fast-reasoning"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"

    # --- Pub/Sub ---
    gcp_project_id: str | None = None
    listing_crawl_topic: str = "listing-crawl"
    event_crawl_topic: str = "event-crawl"

    # --- Crawl behaviour ---
    respect_robots_txt: bool = True
    user_agent: str = "Mozilla/5.0 (compatible; EventBot/1.0; +https://example.invalid/bot)"


settings = Settings()
