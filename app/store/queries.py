"""The analytical surface of Greenlight Studio.

Every function here is exposed twice: as an ADK FunctionTool to the agents, and
as an MCP tool to any external client. They all return plain dicts carrying the
measured ClickHouse latency, so the number shown in the UI is the number the
database actually took - never a constant.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Literal, Optional

from app.config import settings
from app.store import schema
from app.store.clickhouse import LocalStore, QueryResult, StoreUnavailable, get_store

logger = logging.getLogger("greenlight.queries")

Mode = Literal["film", "series"]

_TABLE = {"film": schema.MOVIES, "series": schema.SERIES}
_TITLE_COL = {"film": "title", "series": "name"}
_YEAR_COL = {"film": "release_year", "series": "first_air_year"}


# --------------------------------------------------------------------------
# guards
# --------------------------------------------------------------------------

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|ATTACH|DETACH|RENAME|"
    r"GRANT|REVOKE|OPTIMIZE|SYSTEM|KILL)\b",
    re.IGNORECASE,
)
_ALLOWED_TABLES = {schema.MOVIES, schema.SERIES, schema.TALENT}


class UnsafeQuery(ValueError):
    pass


def guard_sql(sql: str) -> str:
    """Reject anything that is not a bounded, read-only SELECT."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise UnsafeQuery("Empty query.")
    if ";" in stripped:
        raise UnsafeQuery("Multiple statements are not allowed.")
    if not re.match(r"^(SELECT|WITH)\b", stripped, re.IGNORECASE):
        raise UnsafeQuery("Only SELECT / WITH queries are allowed.")
    if _FORBIDDEN.search(stripped):
        raise UnsafeQuery("Write or DDL keywords are not allowed.")
    referenced = set(re.findall(r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)", stripped, re.IGNORECASE))
    referenced |= set(re.findall(r"\bJOIN\s+([A-Za-z_][A-Za-z0-9_]*)", stripped, re.IGNORECASE))
    unknown = referenced - _ALLOWED_TABLES
    if unknown:
        raise UnsafeQuery(
            f"Unknown table(s): {sorted(unknown)}. Allowed: {sorted(_ALLOWED_TABLES)}."
        )
    if not re.search(r"\bLIMIT\b", stripped, re.IGNORECASE):
        stripped = f"{stripped}\nLIMIT {settings().max_sql_rows}"
    return stripped


# --------------------------------------------------------------------------
# schema description - handed to the Quant Analyst so it can write valid SQL
# --------------------------------------------------------------------------

SCHEMA_CARD = f"""
ClickHouse database. Read-only. Three tables:

{schema.MOVIES} (theatrical features, real TMDB records with non-zero budget and revenue)
  tmdb_id UInt32, title String, original_title String,
  original_language LowCardinality(String)  -- ISO-639-1, e.g. 'en', 'ja', 'ko'
  origin_country Array(LowCardinality(String)),
  release_date Date, release_year UInt16, release_month UInt8,
  genres Array(LowCardinality(String)),      -- use has(genres, 'Drama')
  runtime_min UInt16, budget_usd UInt64, revenue_usd UInt64,
  roi_multiple Float32,                      -- revenue_usd / budget_usd
  profitable UInt8,                          -- 1 when roi_multiple >= 2.5
  vote_average Float32, vote_count UInt32, popularity Float32,
  overview String, tagline String, embedding Array(Float32)

{schema.SERIES} (scripted television, real TMDB records)
  tmdb_id UInt32, name String, original_name String,
  original_language LowCardinality(String), origin_country Array(LowCardinality(String)),
  first_air_date Date, last_air_date Date, first_air_year UInt16,
  status LowCardinality(String),             -- 'Ended' | 'Canceled' | 'Returning Series' | ...
  series_type LowCardinality(String),        -- 'Scripted' | 'Miniseries' | ...
  in_production UInt8, number_of_seasons UInt16, number_of_episodes UInt16,
  episode_run_time UInt16, genres Array(LowCardinality(String)), networks Array(String),
  vote_average Float32, vote_count UInt32, popularity Float32, overview String,
  renewed_beyond_s1 UInt8,                   -- 1 when number_of_seasons >= 2
  cancelled UInt8,                           -- 1 when status = 'Canceled'
  embedding Array(Float32)

{schema.TALENT} (per-person box-office record, derived from film credits)
  person_id UInt32, name String,
  role_type LowCardinality(String),          -- 'director' | 'actor'
  primary_genre LowCardinality(String), credits UInt16,
  avg_roi_multiple Float32, median_roi_multiple Float32,
  avg_revenue_usd UInt64, hit_rate_pct Float32

Notes:
  - Money is nominal USD, not inflation adjusted. Say so if it matters.
  - cosineDistance(embedding, [...]) gives tone similarity; smaller is closer.
  - Always aggregate; do not return raw embedding columns.
""".strip()


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------

_COMP_COLUMNS = {
    "film": """tmdb_id, title, release_year, release_month, genres, original_language,
               runtime_min, budget_usd, revenue_usd, roi_multiple, profitable,
               vote_average, vote_count, overview""",
    "series": """tmdb_id, name AS title, first_air_year AS release_year, genres,
                 original_language, status, number_of_seasons, number_of_episodes,
                 episode_run_time, networks, renewed_beyond_s1, cancelled,
                 vote_average, vote_count, overview""",
}


def find_comparable_titles(
    embedding: list[float],
    mode: Mode = "film",
    genre: Optional[str] = None,
    language: Optional[str] = None,
    min_year: Optional[int] = None,
    limit: int = 6,
) -> dict:
    """Nearest neighbours by tone/subject embedding, filtered on hard attributes."""
    store = get_store()
    table = _TABLE[mode]
    limit = max(1, min(int(limit), 25))

    if isinstance(store, LocalStore):
        return store.vector_search(
            table=table, embedding=embedding, mode=mode,
            genre=genre, language=language, min_year=min_year, limit=limit,
        )

    where: list[str] = []
    params: dict[str, Any] = {"emb": embedding, "lim": limit}
    if genre:
        where.append("has(genres, %(genre)s)")
        params["genre"] = genre
    if language:
        where.append("original_language = %(lang)s")
        params["lang"] = language
    if min_year:
        where.append(f"{_YEAR_COL[mode]} >= %(min_year)s")
        params["min_year"] = int(min_year)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    sql = f"""
        SELECT {_COMP_COLUMNS[mode]},
               round(cosineDistance(embedding, %(emb)s), 4) AS tone_distance
        FROM {table}
        {where_sql}
        ORDER BY tone_distance ASC
        LIMIT %(lim)s
    """
    return store.query(sql, params).as_tool_payload()


def genre_benchmark(
    mode: Mode = "film",
    genre: Optional[str] = None,
    language: Optional[str] = None,
    min_year: Optional[int] = None,
    budget_band_usd: Optional[list[int]] = None,
) -> dict:
    """Distribution of outcomes for a slice of the catalogue."""
    store = get_store()
    if isinstance(store, LocalStore):
        return store.benchmark(mode=mode, genre=genre, language=language, min_year=min_year,
                               budget_band_usd=budget_band_usd)

    table = _TABLE[mode]
    where: list[str] = []
    params: dict[str, Any] = {}
    if genre:
        where.append("has(genres, %(genre)s)")
        params["genre"] = genre
    if language:
        where.append("original_language = %(lang)s")
        params["lang"] = language
    if min_year:
        where.append(f"{_YEAR_COL[mode]} >= %(min_year)s")
        params["min_year"] = int(min_year)
    if budget_band_usd and mode == "film":
        where.append("budget_usd BETWEEN %(blo)s AND %(bhi)s")
        params["blo"], params["bhi"] = int(budget_band_usd[0]), int(budget_band_usd[1])
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    if mode == "film":
        sql = f"""
            SELECT count() AS sample_size,
                   round(avg(budget_usd))                      AS avg_budget_usd,
                   round(median(budget_usd))                   AS median_budget_usd,
                   round(avg(revenue_usd))                     AS avg_revenue_usd,
                   round(avg(roi_multiple), 3)                 AS avg_roi_multiple,
                   round(median(roi_multiple), 3)              AS median_roi_multiple,
                   round(quantile(0.25)(roi_multiple), 3)      AS p25_roi_multiple,
                   round(quantile(0.75)(roi_multiple), 3)      AS p75_roi_multiple,
                   round(100 * countIf(roi_multiple >= 1.0) / count(), 1) AS pct_recouped_budget,
                   round(100 * countIf(profitable) / count(), 1)          AS pct_hit,
                   round(avg(vote_average), 2)                 AS avg_score
            FROM {table} {where_sql}
        """
    else:
        sql = f"""
            SELECT count() AS sample_size,
                   round(avg(number_of_seasons), 2)            AS avg_seasons,
                   round(median(number_of_seasons), 1)         AS median_seasons,
                   round(avg(number_of_episodes), 1)           AS avg_episodes,
                   round(100 * countIf(renewed_beyond_s1) / count(), 1) AS pct_renewed_beyond_s1,
                   round(100 * countIf(cancelled) / count(), 1)         AS pct_cancelled,
                   round(100 * countIf(number_of_seasons >= 3) / count(), 1) AS pct_reached_s3,
                   round(avg(vote_average), 2)                 AS avg_score
            FROM {table} {where_sql}
        """
    return store.query(sql, params).as_tool_payload()


def talent_leaderboard(
    genre: str,
    role_type: str = "director",
    limit: int = 5,
    min_credits: int = 2,
) -> dict:
    """Directors or actors ranked by realised box-office multiple in a genre."""
    store = get_store()
    limit = max(1, min(int(limit), 20))
    if isinstance(store, LocalStore):
        return store.talent(genre=genre, role_type=role_type, limit=limit, min_credits=min_credits)

    sql = f"""
        SELECT name, role_type, primary_genre, credits,
               avg_roi_multiple, median_roi_multiple, avg_revenue_usd, hit_rate_pct
        FROM {schema.TALENT}
        WHERE role_type = %(role)s AND primary_genre = %(genre)s AND credits >= %(minc)s
        ORDER BY median_roi_multiple DESC, avg_revenue_usd DESC
        LIMIT %(lim)s
    """
    return store.query(
        sql,
        {"role": role_type, "genre": genre, "minc": int(min_credits), "lim": limit},
    ).as_tool_payload()


def run_sql(sql: str) -> dict:
    """Execute an agent-authored read-only SELECT against ClickHouse.

    This is the tool that makes the system agentic rather than scripted: the
    model decides what to ask, writes the SQL itself, and sees the real rows.
    """
    store = get_store()
    if isinstance(store, LocalStore):
        raise StoreUnavailable(
            "Ad-hoc SQL requires a live ClickHouse connection (CLICKHOUSE_HOST is unset)."
        )
    safe = guard_sql(sql)
    logger.info("QUANT-SQL %s", safe.replace("\n", " ")[:400])
    return store.query(safe).as_tool_payload()


def catalogue_summary() -> dict:
    """What the agent is actually allowed to claim about coverage."""
    store = get_store()
    if isinstance(store, LocalStore):
        return store.summary()
    sql = f"""
        SELECT 'film' AS mode, count() AS rows,
               min(release_year) AS first_year, max(release_year) AS last_year,
               uniqExact(original_language) AS languages
        FROM {schema.MOVIES}
        UNION ALL
        SELECT 'series', count(), min(first_air_year), max(first_air_year),
               uniqExact(original_language)
        FROM {schema.SERIES}
    """
    return store.query(sql).as_tool_payload(include_sql=False)
