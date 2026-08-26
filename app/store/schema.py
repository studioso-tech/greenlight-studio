"""ClickHouse DDL.

Two fact tables (film / series) plus a derived talent table. Both fact tables
carry a Gemini embedding of the logline so the same vector search works for a
screenplay and for a series bible.
"""
from __future__ import annotations

from app.config import settings

MOVIES = "movies_historical"
SERIES = "series_historical"
TALENT = "talent_analytics"


def ddl() -> list[str]:
    dim = settings().embedding_dim
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {MOVIES}
        (
            tmdb_id            UInt32,
            title              String,
            original_title     String,
            original_language  LowCardinality(String),
            origin_country     Array(LowCardinality(String)),
            release_date       Date,
            release_year       UInt16,
            release_month      UInt8,
            genres             Array(LowCardinality(String)),
            runtime_min        UInt16,
            budget_usd         UInt64,
            revenue_usd        UInt64,
            roi_multiple       Float32,
            profitable         UInt8,
            vote_average       Float32,
            vote_count         UInt32,
            popularity         Float32,
            overview           String,
            tagline            String,
            embedding          Array(Float32),
            ingested_at        DateTime DEFAULT now(),
            CONSTRAINT embedding_dim CHECK length(embedding) = {dim}
        )
        ENGINE = ReplacingMergeTree(ingested_at)
        ORDER BY (release_year, tmdb_id)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {SERIES}
        (
            tmdb_id             UInt32,
            name                String,
            original_name       String,
            original_language   LowCardinality(String),
            origin_country      Array(LowCardinality(String)),
            first_air_date      Date,
            last_air_date       Date,
            first_air_year      UInt16,
            status              LowCardinality(String),
            series_type         LowCardinality(String),
            in_production       UInt8,
            number_of_seasons   UInt16,
            number_of_episodes  UInt16,
            episode_run_time    UInt16,
            genres              Array(LowCardinality(String)),
            networks            Array(String),
            vote_average        Float32,
            vote_count          UInt32,
            popularity          Float32,
            overview            String,
            renewed_beyond_s1   UInt8,
            cancelled           UInt8,
            embedding           Array(Float32),
            ingested_at         DateTime DEFAULT now(),
            CONSTRAINT embedding_dim CHECK length(embedding) = {dim}
        )
        ENGINE = ReplacingMergeTree(ingested_at)
        ORDER BY (first_air_year, tmdb_id)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {TALENT}
        (
            person_id           UInt32,
            name                String,
            role_type           LowCardinality(String),
            primary_genre       LowCardinality(String),
            credits             UInt16,
            avg_roi_multiple    Float32,
            median_roi_multiple Float32,
            avg_revenue_usd     UInt64,
            hit_rate_pct        Float32,
            ingested_at         DateTime DEFAULT now()
        )
        ENGINE = ReplacingMergeTree(ingested_at)
        ORDER BY (role_type, primary_genre, person_id)
        """,
    ]


DROP = [f"DROP TABLE IF EXISTS {t}" for t in (MOVIES, SERIES, TALENT)]
