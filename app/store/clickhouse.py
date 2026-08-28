"""ClickHouse Cloud access layer.

Design rule: this submission is entered on the ClickHouse track, so the
production build must answer from ClickHouse or fail loudly. The in-memory
LocalStore exists only so the project can be developed before credentials are
issued, and it is refused unless ALLOW_LOCAL_FALLBACK is set. Whichever mode is
active is reported verbatim on /api/health.
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.config import settings
from app.store import schema

logger = logging.getLogger("greenlight.clickhouse")

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class StoreUnavailable(RuntimeError):
    pass


def sanitise(value: Any) -> Any:
    """Strip values that are legal in ClickHouse but not in JSON.

    An aggregate over an empty set returns NaN, and NaN is not valid JSON. Left
    alone it reaches the model as a malformed payload and the request dies with
    a 400 - which is exactly what happened the first time an agent filtered a
    segment down to zero rows.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: sanitise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitise(v) for v in value]
    return value


@dataclass
class QueryResult:
    rows: list[dict[str, Any]]
    elapsed_ms: float
    sql: str
    backend: str

    def as_tool_payload(self, include_sql: bool = True) -> dict:
        payload: dict[str, Any] = {
            "rows": self.rows,
            "row_count": len(self.rows),
            "elapsed_ms": round(self.elapsed_ms, 2),
            "backend": self.backend,
        }
        if include_sql:
            payload["sql"] = self.sql
        return payload


class ClickHouseStore:
    """Thin wrapper that keeps every timing measurement honest."""

    backend = "clickhouse"

    def __init__(self) -> None:
        import clickhouse_connect

        s = settings()
        self._client = clickhouse_connect.get_client(
            host=s.ch_host,
            port=s.ch_port,
            username=s.ch_user,
            password=s.ch_password,
            secure=s.ch_secure,
            database=s.ch_database,
            connect_timeout=s.ch_connect_timeout,
            send_receive_timeout=s.ch_query_timeout,
            client_name="greenlight-studio",
        )
        self.server_version = self._client.server_version
        logger.info(
            "ClickHouse connected host=%s version=%s db=%s",
            s.ch_host, self.server_version, s.ch_database,
        )

    def query(self, sql: str, parameters: Optional[dict] = None) -> QueryResult:
        t0 = time.perf_counter()
        result = self._client.query(sql, parameters=parameters or {})
        elapsed = (time.perf_counter() - t0) * 1000
        cols = result.column_names
        rows = [sanitise(dict(zip(cols, r))) for r in result.result_rows]
        return QueryResult(rows=rows, elapsed_ms=elapsed, sql=sql.strip(), backend=self.backend)

    def command(self, sql: str) -> None:
        self._client.command(sql)

    def insert_rows(self, table: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        columns = list(rows[0].keys())
        data = [[r[c] for c in columns] for r in rows]
        self._client.insert(table, data, column_names=columns)
        return len(rows)

    def create_schema(self) -> None:
        for stmt in schema.ddl():
            self._client.command(stmt)
        logger.info("ClickHouse schema ensured")

    def table_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in (schema.MOVIES, schema.SERIES, schema.TALENT):
            try:
                out[t] = int(self.query(f"SELECT count() AS c FROM {t}").rows[0]["c"])
            except Exception:  # table not created yet
                out[t] = -1
        return out


class LocalStore:
    """Development stand-in. Reads the same JSONL the ingest step writes."""

    backend = "local-memory"
    server_version = None

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        for name in (schema.MOVIES, schema.SERIES, schema.TALENT):
            path = DATA_DIR / f"{name}.jsonl"
            rows: list[dict] = []
            if path.exists():
                with path.open("r", encoding="utf-8") as fh:
                    rows = [json.loads(line) for line in fh if line.strip()]
            self.tables[name] = rows
        logger.warning(
            "LOCAL FALLBACK ACTIVE - this is not ClickHouse. counts=%s",
            {k: len(v) for k, v in self.tables.items()},
        )

    def query(self, sql: str, parameters: Optional[dict] = None) -> QueryResult:
        raise StoreUnavailable(
            "Ad-hoc SQL requires ClickHouse. Set CLICKHOUSE_HOST to enable the Quant Analyst agent."
        )

    # -- read paths that dev mode still supports -------------------------
    # These mirror the ClickHouse SQL in app/store/queries.py closely enough
    # to build against, and are always labelled backend="local-memory".

    def _filtered(self, table: str, mode: str, genre=None, language=None,
                  min_year=None, budget_band_usd=None) -> list[dict]:
        year_col = "release_year" if mode == "film" else "first_air_year"
        rows = self.tables.get(table, [])
        if genre:
            rows = [r for r in rows if genre in (r.get("genres") or [])]
        if language:
            rows = [r for r in rows if r.get("original_language") == language]
        if min_year:
            rows = [r for r in rows if (r.get(year_col) or 0) >= int(min_year)]
        if budget_band_usd and mode == "film":
            lo, hi = int(budget_band_usd[0]), int(budget_band_usd[1])
            rows = [r for r in rows if lo <= (r.get("budget_usd") or 0) <= hi]
        return rows

    def vector_search(self, table: str, embedding: list, mode: str, genre=None,
                      language=None, min_year=None, limit: int = 6) -> dict:
        import numpy as np

        t0 = time.perf_counter()
        rows = self._filtered(table, mode, genre, language, min_year)
        if not rows:
            return QueryResult([], (time.perf_counter() - t0) * 1000, "", self.backend).as_tool_payload(False)
        target = np.asarray(embedding, dtype="float32")
        matrix = np.asarray([r["embedding"] for r in rows], dtype="float32")
        denom = np.linalg.norm(matrix, axis=1) * np.linalg.norm(target) + 1e-9
        distance = 1.0 - (matrix @ target) / denom
        order = np.argsort(distance)[:limit]
        out = []
        for i in order:
            r = dict(rows[int(i)])
            r.pop("embedding", None)
            if mode == "series":
                r["release_year"] = r.get("first_air_year")
            r["tone_distance"] = round(float(distance[int(i)]), 4)
            out.append(r)
        return QueryResult(out, (time.perf_counter() - t0) * 1000, "", self.backend).as_tool_payload(False)

    @staticmethod
    def _pct(rows: list[dict], pred) -> float:
        return round(100 * sum(1 for r in rows if pred(r)) / len(rows), 1) if rows else 0.0

    def benchmark(self, mode: str, genre=None, language=None, min_year=None,
                  budget_band_usd=None) -> dict:
        import statistics

        t0 = time.perf_counter()
        table = schema.MOVIES if mode == "film" else schema.SERIES
        rows = self._filtered(table, mode, genre, language, min_year, budget_band_usd)
        if not rows:
            return QueryResult([{"sample_size": 0}], (time.perf_counter() - t0) * 1000,
                               "", self.backend).as_tool_payload(False)
        if mode == "film":
            roi = [float(r.get("roi_multiple") or 0) for r in rows]
            stat = {
                "sample_size": len(rows),
                "avg_budget_usd": round(statistics.fmean(r["budget_usd"] for r in rows)),
                "median_budget_usd": round(statistics.median(r["budget_usd"] for r in rows)),
                "avg_revenue_usd": round(statistics.fmean(r["revenue_usd"] for r in rows)),
                "avg_roi_multiple": round(statistics.fmean(roi), 3),
                "median_roi_multiple": round(statistics.median(roi), 3),
                "pct_recouped_budget": self._pct(rows, lambda r: (r.get("roi_multiple") or 0) >= 1.0),
                "pct_hit": self._pct(rows, lambda r: r.get("profitable")),
                "avg_audience_score": round(statistics.fmean(
                    [r["audience_score"] for r in rows if r.get("has_audience_score")] or [0.0]), 1),
            }
        else:
            seasons = [r.get("number_of_seasons") or 0 for r in rows]
            stat = {
                "sample_size": len(rows),
                "avg_seasons": round(statistics.fmean(seasons), 2),
                "median_seasons": round(statistics.median(seasons), 1),
                "avg_episodes": round(statistics.fmean(r.get("number_of_episodes") or 0 for r in rows), 1),
                "pct_returned_after_s1": self._pct(rows, lambda r: r.get("returned_after_s1")),
                "pct_did_not_return": self._pct(rows, lambda r: r.get("did_not_return")),
                "pct_reached_s3": self._pct(rows, lambda r: (r.get("number_of_seasons") or 0) >= 3),
                "pct_still_running": self._pct(rows, lambda r: r.get("still_running")),
            }
        return QueryResult([stat], (time.perf_counter() - t0) * 1000, "", self.backend).as_tool_payload(False)

    def talent(self, genre: str, role_type: str, limit: int, min_credits: int) -> dict:
        t0 = time.perf_counter()
        rows = [
            r for r in self.tables.get(schema.TALENT, [])
            if r.get("role_type") == role_type
            and r.get("primary_genre") == genre
            and (r.get("credits") or 0) >= min_credits
            and not r.get("death_year")
        ]
        rows.sort(key=lambda r: (r.get("median_roi_multiple") or 0, r.get("avg_revenue_usd") or 0), reverse=True)
        return QueryResult(rows[:limit], (time.perf_counter() - t0) * 1000,
                           "", self.backend).as_tool_payload(False)

    def summary(self) -> dict:
        t0 = time.perf_counter()
        out = []
        for mode, table, year_col in (("film", schema.MOVIES, "release_year"),
                                      ("series", schema.SERIES, "first_air_year")):
            rows = self.tables.get(table, [])
            years = [r.get(year_col) for r in rows if r.get(year_col)]
            out.append({
                "mode": mode,
                "rows": len(rows),
                "first_year": min(years) if years else None,
                "last_year": max(years) if years else None,
                "languages": len({r.get("original_language") for r in rows}),
            })
        return QueryResult(out, (time.perf_counter() - t0) * 1000, "", self.backend).as_tool_payload(False)

    def command(self, sql: str) -> None:
        raise StoreUnavailable("DDL requires ClickHouse.")

    def insert_rows(self, table: str, rows: list[dict]) -> int:
        raise StoreUnavailable("Inserts require ClickHouse.")

    def create_schema(self) -> None:
        raise StoreUnavailable("DDL requires ClickHouse.")

    def table_counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.tables.items()}


_store: ClickHouseStore | LocalStore | None = None
_store_error: str | None = None


def get_store():
    global _store, _store_error
    if _store is not None:
        return _store
    s = settings()
    if s.clickhouse_configured:
        try:
            _store = ClickHouseStore()
            return _store
        except Exception as exc:  # noqa: BLE001
            _store_error = f"{type(exc).__name__}: {exc}"
            logger.error("ClickHouse connection failed: %s", _store_error)
            if not s.allow_local_fallback:
                raise
    else:
        _store_error = "CLICKHOUSE_HOST is not set"
        if not s.allow_local_fallback:
            raise StoreUnavailable(
                "CLICKHOUSE_HOST is not set and ALLOW_LOCAL_FALLBACK is off."
            )
    _store = LocalStore()
    return _store


def reset_store() -> None:
    global _store, _store_error
    _store = None
    _store_error = None


def store_status() -> dict:
    try:
        store = get_store()
    except Exception as exc:  # noqa: BLE001
        return {"backend": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    return {
        "backend": store.backend,
        "server_version": store.server_version,
        "tables": store.table_counts(),
        "last_error": _store_error,
    }
