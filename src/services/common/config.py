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
        # .env lives at src/services/.env (one level above this common/ package, see
        # Dockerfile - the whole services/ tree is copied as one unit) - not
        # Path(__file__).parent, which would now point at common/ itself.
        env_file=Path(__file__).parent.parent / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Storage ---
    # Postgres, hosted on Neon (serverless Postgres - see ARCHITECTURE.md's "Database"
    # section). This is Neon's *pooled* connection string (host has a "-pooler" suffix) -
    # what the crawler workers and the admin web interface should both use, since both are
    # bursty/many-short-lived-connections workloads, exactly what Neon's PgBouncer-style
    # pooler is for. sslmode=require because Neon requires TLS. Example (see the Neon
    # dashboard's "Connect" panel for the real host/user/password/dbname):
    # postgresql+psycopg://user:password@ep-xxx-pooler.region.aws.neon.tech/events?sslmode=require
    # Defaults to a plain local Postgres for local dev without a Neon project at all.
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/events"
    # Neon's *unpooled* connection string (same host, minus "-pooler") - only used for
    # session-mode DDL (common/db.py's _add_missing_columns() runs ALTER TABLE, which
    # doesn't reliably work through a transaction-mode pooler). Leave unset for local
    # Postgres (no pooled/unpooled distinction there) - db.py falls back to database_url.
    database_url_unpooled: str | None = None

    # --- Firecrawl ---
    firecrawl_api_key: str | None = None
    # Only set this for a self-hosted Firecrawl instance. Leave unset to use
    # Firecrawl's hosted cloud API, which is what handles proxy rotation /
    # anti-bot for us instead of a local VPN.
    firecrawl_api_url: str | None = None

    # --- LLM extraction (pluggable) ---
    llm_provider: str = "grok"  # "grok", "anthropic", or "local"
    grok_api_key: str | None = None
    grok_model: str = "grok-4-1-fast-reasoning"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    # "local" - a self-hosted Ollama model, no per-token cost and no API key. Not used by
    # the real pipeline (see llm/client.py's module docstring for why) - exists purely
    # so tests/llm/local/ can exercise the real extraction prompts against a real model
    # without paying for or depending on Grok/Anthropic. Defaults match what's already
    # pulled in this dev environment; override via LOCAL_LLM_MODEL/.env for a different one.
    local_llm_model: str = "qwen2.5:7b"
    local_llm_base_url: str = "http://localhost:11434"

    # --- Pub/Sub ---
    gcp_project_id: str | None = None
    listing_crawl_topic: str = "listing-crawl"
    event_crawl_topic: str = "event-crawl"
    # Separate from the two above: the "structured bulk feed" pipeline (parkrun,
    # future meetup/OSM importers - see feeds/feed_importers.py) is a scheduled,
    # named-source trigger ({"source": "parkrun", "params": {...}}), not a
    # per-organiser/per-event fan-out - one topic shared by every importer, dispatched
    # by source name.
    feed_import_topic: str = "feed-import"

    # --- Crawl behaviour ---
    respect_robots_txt: bool = True
    user_agent: str = "Mozilla/5.0 (compatible; EventBot/1.0; +https://example.invalid/bot)"

    # --- Scraper backend (see scraping/backends/scraper_client.py) ---
    # "crawl4ai" (default): self-hosted headless browser, no per-page cost - used for
    # every organiser, falling back to Firecrawl automatically on failure.
    # "firecrawl": skip crawl4ai entirely and always use Firecrawl - an escape hatch to
    # roll back instantly (e.g. self-hosting turns out unreliable) without a code change.
    scraper_backend: str = "crawl4ai"

    # --- Admin web interface (see admin/web/app.py) ---
    # Optional HTTP Basic gate - enforced only when BOTH are set (see app.py's
    # _require_auth). This is a second layer, not a replacement for, restricting who can
    # invoke the Cloud Run service in the first place (see INSTALLATION.md) - the admin
    # interface is a raw view over the full database and was never meant to be public.
    admin_basic_auth_user: str | None = None
    admin_basic_auth_password: str | None = None


settings = Settings()
