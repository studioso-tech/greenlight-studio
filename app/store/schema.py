"""ClickHouse DDL.

Source: Wikidata (CC0) for the structured facts, English Wikipedia (CC BY-SA
4.0) for the synopsis text that gets embedded. Both are chosen deliberately:
the catalogue carries no licence that would restrict feeding it to a model or
running the result inside a business, which is not true of the commercial film
databases.

Two fact tables (film / series) plus a derived talent table. Both fact tables
carry a Gemini embedding of the synopsis, so the same vector search works for a
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
            wikidata_id        String,
            title              String,
            title_ja           String,
            original_language  LowCardinality(String),
            origin_country     Array(LowCardinality(String)),
            release_date       Date32,   -- Date starts at 1970; the catalogue starts in 1921
            release_year       UInt16,
            release_month      UInt8,
            genres             Array(LowCardinality(String)),
            runtime_min        UInt16,
            budget_usd         UInt64,
            revenue_usd        UInt64,
            roi_multiple       Float32,
            profitable         UInt8,
            audience_score     Float32,   -- 0-100, normalised from Wikidata P444
            has_audience_score UInt8,
            synopsis           String,
            embedding          Array(Float32),
            ingested_at        DateTime DEFAULT now(),
            CONSTRAINT embedding_dim CHECK length(embedding) = {dim}
        )
        ENGINE = ReplacingMergeTree(ingested_at)
        ORDER BY (release_year, wikidata_id)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {SERIES}
        (
            wikidata_id         String,
            title               String,
            title_ja            String,
            original_language   LowCardinality(String),
            origin_country      Array(LowCardinality(String)),
            first_air_date      Date32,   -- Date starts at 1970; the catalogue starts in 1937
            last_air_date       Date32,
            first_air_year      UInt16,
            number_of_seasons   UInt16,
            number_of_episodes  UInt16,
            genres              Array(LowCardinality(String)),
            networks            Array(String),
            audience_score      Float32,
            has_audience_score  UInt8,
            synopsis            String,
            -- Wikidata has no 'cancelled' flag, so the outcome labels are
            -- derived from what it does record. Definitions are stated on the
            -- report so nobody has to guess what they mean.
            returned_after_s1   UInt8,   -- number_of_seasons >= 2
            has_ended           UInt8,   -- an end date is recorded
            did_not_return      UInt8,   -- one season and then it ended
            still_running       UInt8,   -- no end date recorded
            embedding           Array(Float32),
            ingested_at         DateTime DEFAULT now(),
            CONSTRAINT embedding_dim CHECK length(embedding) = {dim}
        )
        ENGINE = ReplacingMergeTree(ingested_at)
        ORDER BY (first_air_year, wikidata_id)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {TALENT}
        (
            wikidata_id         String,
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
        ORDER BY (role_type, primary_genre, wikidata_id)
        """,
    ]


DROP = [f"DROP TABLE IF EXISTS {t}" for t in (MOVIES, SERIES, TALENT)]
