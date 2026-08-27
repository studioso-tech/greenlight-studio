"""Tools the agents may call.

Each one wraps a ClickHouse query, records the measured latency into the request
trace, and returns a compact dict. Docstrings are the tool descriptions the
model actually reads, so they are written for the model, not for us.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.agents.context import ToolCall, current
from app.store import queries
from app.store.clickhouse import StoreUnavailable

logger = logging.getLogger("greenlight.tools")


def _record(tool: str, arguments: dict[str, Any], payload: dict, error: str | None = None) -> None:
    ctx = current()
    ctx.record(
        ToolCall(
            agent=ctx.current_agent,
            tool=tool,
            arguments={k: v for k, v in arguments.items() if k != "embedding"},
            elapsed_ms=float(payload.get("elapsed_ms", 0.0)),
            backend=payload.get("backend", "none"),
            row_count=int(payload.get("row_count", 0)),
            sql=payload.get("sql"),
            error=error,
        )
    )


def _failed(tool: str, arguments: dict, exc: Exception, started: float) -> dict:
    payload = {
        "error": f"{type(exc).__name__}: {exc}",
        "elapsed_ms": (time.perf_counter() - started) * 1000,
        "backend": "none",
        "row_count": 0,
    }
    _record(tool, arguments, payload, error=payload["error"])
    logger.warning("tool %s failed: %s", tool, payload["error"])
    return payload


def find_comparable_titles(
    genre: str = "",
    language: str = "",
    min_year: int = 0,
    limit: int = 6,
) -> dict:
    """Find the titles most similar in tone and subject to the project under review.

    Similarity is a cosine distance over Gemini embeddings of the loglines,
    computed inside ClickHouse. Smaller tone_distance means closer.

    Args:
        genre: Restrict to one genre, e.g. "Science Fiction", "Drama". Empty means any.
        language: Original language as its English NAME - "English", "Japanese",
            "Korean", "Russian", "Spanish", "French". Not an ISO code. Empty means any.
        min_year: Only titles released in or after this year. 0 means no limit.
        limit: How many comparables to return, 1-25.

    Returns:
        rows: the comparable titles with their real budgets, outcomes and scores.
    """
    ctx = current()
    args = {"genre": genre, "language": language, "min_year": min_year, "limit": limit}
    started = time.perf_counter()
    try:
        payload = queries.find_comparable_titles(
            embedding=ctx.embedding,
            mode=ctx.mode,
            genre=genre or None,
            language=language or None,
            min_year=min_year or None,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        return _failed("find_comparable_titles", args, exc, started)
    _record("find_comparable_titles", args, payload)
    rows = payload.get("rows") or []
    # Keep the widest comp set the agent found; the scorer reads this, not prose.
    if len(rows) > len(ctx.comps):
        ctx.comps = rows
    return payload


def benchmark_segment(
    genre: str = "",
    language: str = "",
    min_year: int = 0,
    budget_floor_usd: int = 0,
    budget_ceiling_usd: int = 0,
) -> dict:
    """Aggregate how a whole slice of the catalogue performed.

    For film this returns budget and ROI distribution plus the share that
    recouped. For series it returns renewal rate, cancellation rate and how many
    reached a third season.

    Args:
        genre: Restrict to one genre. Empty means the whole catalogue.
        language: Original language as its English NAME, e.g. "Japanese". Empty means any.
        min_year: Only titles from this year onward. 0 means no limit.
        budget_floor_usd: Lower bound of a budget band (film only). 0 disables the band.
        budget_ceiling_usd: Upper bound of a budget band (film only). 0 disables the band.
    """
    ctx = current()
    band = None
    if budget_floor_usd and budget_ceiling_usd:
        band = [budget_floor_usd, budget_ceiling_usd]
    args = {
        "genre": genre, "language": language, "min_year": min_year,
        "budget_band_usd": band,
    }
    started = time.perf_counter()
    try:
        payload = queries.genre_benchmark(
            mode=ctx.mode,
            genre=genre or None,
            language=language or None,
            min_year=min_year or None,
            budget_band_usd=band,
        )
    except Exception as exc:  # noqa: BLE001
        return _failed("benchmark_segment", args, exc, started)
    _record("benchmark_segment", args, payload)
    rows = payload.get("rows") or []
    if rows and not ctx.benchmark:
        ctx.benchmark = rows[0]
    return payload


def rank_talent(genre: str, role_type: str = "director", limit: int = 5) -> dict:
    """Rank directors or actors by the box-office multiple they actually delivered in a genre.

    Args:
        genre: The genre to rank within, e.g. "Thriller".
        role_type: Either "director" or "actor".
        limit: How many people to return, 1-20.
    """
    args = {"genre": genre, "role_type": role_type, "limit": limit}
    started = time.perf_counter()
    try:
        payload = queries.talent_leaderboard(genre=genre, role_type=role_type, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return _failed("rank_talent", args, exc, started)
    _record("rank_talent", args, payload)
    if payload.get("rows"):
        current().talent = payload["rows"]
    return payload


def query_catalogue(sql: str) -> dict:
    """Run your own read-only SQL against the ClickHouse catalogue.

    Use this when the other tools cannot answer the question you actually have,
    for example comparing release months, testing whether a budget band behaves
    differently in one language, or checking how a network's cancellation rate
    changed over time.

    Rules enforced by the server: a single SELECT or WITH statement, only the
    tables described in your instructions, no writes, and a row limit is added
    if you omit one. If the query is rejected, read the error and rewrite it.

    Args:
        sql: The SQL to execute.
    """
    args = {"sql": sql}
    started = time.perf_counter()
    try:
        payload = queries.run_sql(sql)
    except StoreUnavailable as exc:
        return _failed("query_catalogue", args, exc, started)
    except queries.UnsafeQuery as exc:
        return _failed("query_catalogue", args, exc, started)
    except Exception as exc:  # noqa: BLE001
        return _failed("query_catalogue", args, exc, started)
    _record("query_catalogue", args, payload)
    return payload


def describe_catalogue() -> dict:
    """Report how many titles the catalogue holds, which years and how many languages.

    Call this before making any claim about coverage, so the memo never
    overstates what the data supports.
    """
    started = time.perf_counter()
    try:
        payload = queries.catalogue_summary()
    except Exception as exc:  # noqa: BLE001
        return _failed("describe_catalogue", {}, exc, started)
    _record("describe_catalogue", {}, payload)
    return payload


RESEARCH_TOOLS = [
    find_comparable_titles,
    benchmark_segment,
    rank_talent,
    query_catalogue,
    describe_catalogue,
]
