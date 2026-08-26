"""Central configuration. Every external dependency is declared here."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    # --- Google Cloud / Gemini ---------------------------------------------
    gcp_project: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    gcp_location: str = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    use_vertex: bool = _flag("GOOGLE_GENAI_USE_VERTEXAI", True)
    gemini_api_key: str = os.getenv("GOOGLE_API_KEY", "")

    reasoning_model: str = os.getenv("GEMINI_REASONING_MODEL", "gemini-2.5-flash")
    writer_model: str = os.getenv("GEMINI_WRITER_MODEL", "gemini-2.5-flash")
    embedding_model: str = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    # gemini-embedding-001 is Matryoshka-trained: truncating to 768 keeps quality
    # while cutting ClickHouse storage and cosineDistance cost by 4x.
    embedding_dim: int = int(os.getenv("GEMINI_EMBEDDING_DIM", "768"))

    # --- ClickHouse ---------------------------------------------------------
    ch_host: str = os.getenv("CLICKHOUSE_HOST", "")
    ch_port: int = int(os.getenv("CLICKHOUSE_PORT", "8443"))
    ch_user: str = os.getenv("CLICKHOUSE_USER", "default")
    ch_password: str = os.getenv("CLICKHOUSE_PASSWORD", "")
    ch_database: str = os.getenv("CLICKHOUSE_DATABASE", "greenlight")
    ch_secure: bool = _flag("CLICKHOUSE_SECURE", True)
    ch_connect_timeout: int = int(os.getenv("CLICKHOUSE_CONNECT_TIMEOUT", "10"))
    ch_query_timeout: int = int(os.getenv("CLICKHOUSE_QUERY_TIMEOUT", "20"))

    # --- TMDB ---------------------------------------------------------------
    tmdb_api_key: str = os.getenv("TMDB_API_KEY", "")

    # --- Guardrails (see app/cost.py) --------------------------------------
    max_cost_usd_per_request: float = float(os.getenv("MAX_COST_USD_PER_REQUEST", "0.25"))
    max_output_tokens: int = int(os.getenv("MAX_OUTPUT_TOKENS", "2048"))
    max_sql_rows: int = int(os.getenv("MAX_SQL_ROWS", "200"))
    agent_timeout_sec: int = int(os.getenv("AGENT_TIMEOUT_SEC", "90"))

    # --- Behaviour ----------------------------------------------------------
    # Off by default: a submission for the ClickHouse track must not silently
    # answer from a local in-memory copy. Dev machines opt in explicitly.
    allow_local_fallback: bool = _flag("ALLOW_LOCAL_FALLBACK", False)
    default_locale: str = os.getenv("DEFAULT_LOCALE", "en")

    @property
    def clickhouse_configured(self) -> bool:
        return bool(self.ch_host)

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gcp_project) if self.use_vertex else bool(self.gemini_api_key)


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
