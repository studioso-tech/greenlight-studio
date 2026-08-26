"""Build the catalogue from real TMDB records.

Two passes per mode:
  discover -> candidate ids (cheap, paginated)
  detail   -> the fields that actually matter, plus credits for film

Films are kept only when TMDB carries both a budget and a revenue. That throws
away a lot of titles, and the count that survives is reported at the end and
written into data/manifest.json so the README and the UI can state the real
coverage instead of a round number nobody checked.

Usage:
    python -m ingest.tmdb films  --pages 40
    python -m ingest.tmdb series --pages 40
    python -m ingest.tmdb talent
    python -m ingest.tmdb load          # push the JSONL into ClickHouse
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx

from app.config import settings
from app.cost import CostMeter
from app.embeddings import embed_texts
from app.store import schema

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("ingest.tmdb")

BASE = "https://api.themoviedb.org/3"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATA_DIR.mkdir(exist_ok=True)

# TMDB permits bursts well above this; 16 keeps us polite and still finishes a
# few thousand detail calls in a couple of minutes.
CONCURRENCY = 16

# Titles nobody rated carry no usable signal and pollute the comparable sets.
MIN_VOTES_FILM = 50
MIN_VOTES_SERIES = 30

# Languages worth over-sampling so the Japanese and Korean markets are actually
# represented rather than being a rounding error in an English-only catalogue.
EXTRA_LANGUAGES = ["ja", "ko", "fr", "es", "de", "zh"]


class TMDBError(RuntimeError):
    pass


def _client(api_key: str) -> httpx.AsyncClient:
    # TMDB accepts either a v3 api_key query param or a v4 bearer token.
    headers = {"accept": "application/json"}
    params: dict[str, str] = {}
    if api_key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        params["api_key"] = api_key
    return httpx.AsyncClient(
        base_url=BASE, headers=headers, params=params, timeout=30.0,
        limits=httpx.Limits(max_connections=CONCURRENCY * 2),
    )


async def _get(client: httpx.AsyncClient, path: str, **params) -> dict:
    for attempt in range(5):
        response = await client.get(path, params=params)
        if response.status_code == 429:
            wait = float(response.headers.get("retry-after", 1)) + attempt
            logger.warning("rate limited, sleeping %.1fs", wait)
            await asyncio.sleep(wait)
            continue
        if response.status_code == 401:
            raise TMDBError("TMDB rejected the API key (401). Check TMDB_API_KEY.")
        response.raise_for_status()
        return response.json()
    raise TMDBError(f"Gave up on {path} after repeated rate limiting.")


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

async def _discover_ids(client, kind: str, pages: int, min_votes: int,
                        language: Optional[str] = None) -> list[int]:
    ids: list[int] = []
    sort = "revenue.desc" if kind == "movie" else "popularity.desc"
    for page in range(1, pages + 1):
        params: dict[str, Any] = {
            "page": page,
            "sort_by": sort,
            "vote_count.gte": min_votes,
            "include_adult": "false",
        }
        if language:
            params["with_original_language"] = language
            params["sort_by"] = "vote_count.desc"
        if kind == "tv":
            params["with_type"] = 2  # scripted
        data = await _get(client, f"/discover/{kind}", **params)
        results = data.get("results", [])
        if not results:
            break
        ids.extend(r["id"] for r in results)
        if page >= data.get("total_pages", 1):
            break
    return ids


# --------------------------------------------------------------------------
# films
# --------------------------------------------------------------------------

def _film_row(detail: dict) -> Optional[dict]:
    budget = int(detail.get("budget") or 0)
    revenue = int(detail.get("revenue") or 0)
    if budget <= 0 or revenue <= 0:
        return None
    release = detail.get("release_date") or ""
    if len(release) != 10:
        return None
    roi = revenue / budget
    return {
        "tmdb_id": int(detail["id"]),
        "title": detail.get("title") or "",
        "original_title": detail.get("original_title") or "",
        "original_language": detail.get("original_language") or "",
        "origin_country": [c["iso_3166_1"] for c in detail.get("production_countries", [])],
        "release_date": release,
        "release_year": int(release[:4]),
        "release_month": int(release[5:7]),
        "genres": [g["name"] for g in detail.get("genres", [])],
        "runtime_min": int(detail.get("runtime") or 0),
        "budget_usd": budget,
        "revenue_usd": revenue,
        "roi_multiple": round(roi, 4),
        "profitable": 1 if roi >= 2.5 else 0,
        "vote_average": float(detail.get("vote_average") or 0.0),
        "vote_count": int(detail.get("vote_count") or 0),
        "popularity": float(detail.get("popularity") or 0.0),
        "overview": detail.get("overview") or "",
        "tagline": detail.get("tagline") or "",
    }


def _credits_from(detail: dict) -> dict:
    credits = detail.get("credits") or {}
    directors = [
        {"id": c["id"], "name": c["name"]}
        for c in credits.get("crew", [])
        if c.get("job") == "Director"
    ]
    cast = [
        {"id": c["id"], "name": c["name"]}
        for c in sorted(credits.get("cast", []), key=lambda c: c.get("order", 999))[:4]
    ]
    return {"directors": directors, "cast": cast}


async def ingest_films(pages: int) -> None:
    s = settings()
    if not s.tmdb_api_key:
        raise SystemExit("TMDB_API_KEY is not set. Put it in .env and re-run.")

    async with _client(s.tmdb_api_key) as client:
        logger.info("discovering film ids ...")
        ids = await _discover_ids(client, "movie", pages, MIN_VOTES_FILM)
        for lang in EXTRA_LANGUAGES:
            ids += await _discover_ids(client, "movie", max(4, pages // 4), MIN_VOTES_FILM, lang)
        ids = list(dict.fromkeys(ids))
        logger.info("%d unique candidate films", len(ids))

        sem = asyncio.Semaphore(CONCURRENCY)
        rows: list[dict] = []
        credits_by_film: dict[int, dict] = {}
        seen = 0

        async def fetch(mid: int):
            nonlocal seen
            async with sem:
                try:
                    detail = await _get(client, f"/movie/{mid}", append_to_response="credits")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("skip %s: %s", mid, exc)
                    return
            seen += 1
            if seen % 250 == 0:
                logger.info("detail %d/%d, kept %d", seen, len(ids), len(rows))
            row = _film_row(detail)
            if row:
                rows.append(row)
                credits_by_film[row["tmdb_id"]] = _credits_from(detail)

        await asyncio.gather(*(fetch(i) for i in ids))

    logger.info("kept %d films with both budget and revenue (from %d candidates)", len(rows), len(ids))
    _attach_embeddings(rows, lambda r: f"{r['title']}. {r['tagline']} {r['overview']}".strip())
    _write_jsonl(schema.MOVIES, rows)
    _write_json("film_credits", credits_by_film)
    _write_manifest("film", rows, len(ids))


# --------------------------------------------------------------------------
# series
# --------------------------------------------------------------------------

def _series_row(detail: dict) -> Optional[dict]:
    first = detail.get("first_air_date") or ""
    if len(first) != 10:
        return None
    seasons = int(detail.get("number_of_seasons") or 0)
    if seasons <= 0:
        return None
    status = detail.get("status") or ""
    run_times = detail.get("episode_run_time") or []
    last = detail.get("last_air_date") or first
    return {
        "tmdb_id": int(detail["id"]),
        "name": detail.get("name") or "",
        "original_name": detail.get("original_name") or "",
        "original_language": detail.get("original_language") or "",
        "origin_country": detail.get("origin_country") or [],
        "first_air_date": first,
        "last_air_date": last if len(last) == 10 else first,
        "first_air_year": int(first[:4]),
        "status": status,
        "series_type": detail.get("type") or "",
        "in_production": 1 if detail.get("in_production") else 0,
        "number_of_seasons": seasons,
        "number_of_episodes": int(detail.get("number_of_episodes") or 0),
        "episode_run_time": int(run_times[0]) if run_times else 0,
        "genres": [g["name"] for g in detail.get("genres", [])],
        "networks": [n["name"] for n in detail.get("networks", [])],
        "vote_average": float(detail.get("vote_average") or 0.0),
        "vote_count": int(detail.get("vote_count") or 0),
        "popularity": float(detail.get("popularity") or 0.0),
        "overview": detail.get("overview") or "",
        # The two labels that make television predictable at all. TMDB records
        # whether a show came back and whether it was cut short, which is the
        # closest thing to an outcome variable the public data has.
        "renewed_beyond_s1": 1 if seasons >= 2 else 0,
        "cancelled": 1 if status == "Canceled" else 0,
    }


async def ingest_series(pages: int) -> None:
    s = settings()
    if not s.tmdb_api_key:
        raise SystemExit("TMDB_API_KEY is not set. Put it in .env and re-run.")

    async with _client(s.tmdb_api_key) as client:
        logger.info("discovering series ids ...")
        ids = await _discover_ids(client, "tv", pages, MIN_VOTES_SERIES)
        for lang in EXTRA_LANGUAGES:
            ids += await _discover_ids(client, "tv", max(4, pages // 4), MIN_VOTES_SERIES, lang)
        ids = list(dict.fromkeys(ids))
        logger.info("%d unique candidate series", len(ids))

        sem = asyncio.Semaphore(CONCURRENCY)
        rows: list[dict] = []
        seen = 0

        async def fetch(sid: int):
            nonlocal seen
            async with sem:
                try:
                    detail = await _get(client, f"/tv/{sid}")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("skip %s: %s", sid, exc)
                    return
            seen += 1
            if seen % 250 == 0:
                logger.info("detail %d/%d, kept %d", seen, len(ids), len(rows))
            row = _series_row(detail)
            if row:
                rows.append(row)

        await asyncio.gather(*(fetch(i) for i in ids))

    logger.info("kept %d series (from %d candidates)", len(rows), len(ids))
    _attach_embeddings(rows, lambda r: f"{r['name']}. {r['overview']}".strip())
    _write_jsonl(schema.SERIES, rows)
    _write_manifest("series", rows, len(ids))


# --------------------------------------------------------------------------
# talent, derived from the films we already kept
# --------------------------------------------------------------------------

def build_talent() -> None:
    films = _read_jsonl(schema.MOVIES)
    credits = _read_json("film_credits")
    if not films or not credits:
        raise SystemExit("Run 'films' first: talent is derived from the film table.")

    by_person: dict[tuple[int, str, str], list[dict]] = defaultdict(list)
    names: dict[int, str] = {}
    for film in films:
        entry = credits.get(str(film["tmdb_id"])) or credits.get(film["tmdb_id"])
        if not entry:
            continue
        genre = film["genres"][0] if film["genres"] else "Drama"
        for person in entry.get("directors", []):
            by_person[(person["id"], "director", genre)].append(film)
            names[person["id"]] = person["name"]
        for person in entry.get("cast", []):
            by_person[(person["id"], "actor", genre)].append(film)
            names[person["id"]] = person["name"]

    rows = []
    for (pid, role, genre), credited in by_person.items():
        if len(credited) < 2:
            continue
        rois = [f["roi_multiple"] for f in credited]
        rows.append({
            "person_id": pid,
            "name": names[pid],
            "role_type": role,
            "primary_genre": genre,
            "credits": len(credited),
            "avg_roi_multiple": round(statistics.fmean(rois), 4),
            "median_roi_multiple": round(statistics.median(rois), 4),
            "avg_revenue_usd": int(statistics.fmean(f["revenue_usd"] for f in credited)),
            "hit_rate_pct": round(100 * sum(1 for r in rois if r >= 2.5) / len(rois), 1),
        })
    logger.info("built %d talent rows with 2+ credits in a genre", len(rows))
    _write_jsonl(schema.TALENT, rows)


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

def _attach_embeddings(rows: list[dict], text_of) -> None:
    if not rows:
        return
    meter = CostMeter("ingest", ceiling_usd=float("inf"))
    logger.info("embedding %d rows with %s ...", len(rows), settings().embedding_model)
    vectors = embed_texts([text_of(r) for r in rows], meter=meter)
    for row, vector in zip(rows, vectors):
        row["embedding"] = vector
    logger.info("embeddings done: %s", meter.summary())


def _write_jsonl(name: str, rows: list[dict]) -> None:
    path = DATA_DIR / f"{name}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    logger.info("wrote %s (%d rows, %.1f MB)", path, len(rows), path.stat().st_size / 1e6)


def _read_jsonl(name: str) -> list[dict]:
    path = DATA_DIR / f"{name}.jsonl"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _write_json(name: str, obj: Any) -> None:
    (DATA_DIR / f"{name}.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8"
    )


def _read_json(name: str) -> Any:
    path = DATA_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write_manifest(mode: str, rows: list[dict], candidates: int) -> None:
    """What the README and the UI are allowed to claim."""
    manifest = _read_json("manifest") or {}
    year_key = "release_year" if mode == "film" else "first_air_year"
    years = [r[year_key] for r in rows if r.get(year_key)]
    languages = sorted({r["original_language"] for r in rows})
    manifest[mode] = {
        "rows": len(rows),
        "candidates_examined": candidates,
        "first_year": min(years) if years else None,
        "last_year": max(years) if years else None,
        "years_covered": (max(years) - min(years) + 1) if years else 0,
        "languages": len(languages),
        "language_codes": languages,
        "built_on": date.today().isoformat(),
        "source": "TMDB",
    }
    _write_json("manifest", manifest)
    logger.info("manifest[%s] = %s", mode, {k: v for k, v in manifest[mode].items() if k != "language_codes"})


def load_into_clickhouse() -> None:
    from app.store.clickhouse import ClickHouseStore

    if not settings().clickhouse_configured:
        raise SystemExit("CLICKHOUSE_HOST is not set. Put the ClickHouse Cloud details in .env.")
    store = ClickHouseStore()
    store.create_schema()
    for table in (schema.MOVIES, schema.SERIES, schema.TALENT):
        rows = _read_jsonl(table)
        if not rows:
            logger.warning("%s: nothing to load", table)
            continue
        inserted = 0
        for start in range(0, len(rows), 500):
            inserted += store.insert_rows(table, rows[start : start + 500])
            logger.info("%s: %d/%d", table, inserted, len(rows))
    logger.info("counts now: %s", store.table_counts())


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Greenlight catalogue from TMDB.")
    parser.add_argument("stage", choices=["films", "series", "talent", "load", "all"])
    parser.add_argument("--pages", type=int, default=40,
                        help="Discovery pages per query (20 results each).")
    args = parser.parse_args()

    if args.stage in ("films", "all"):
        asyncio.run(ingest_films(args.pages))
    if args.stage in ("series", "all"):
        asyncio.run(ingest_series(args.pages))
    if args.stage in ("talent", "all"):
        build_talent()
    if args.stage in ("load", "all"):
        load_into_clickhouse()


if __name__ == "__main__":
    main()
