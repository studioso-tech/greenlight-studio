"""Build the catalogue from Wikidata (CC0) and English Wikipedia (CC BY-SA 4.0).

Why these sources: neither carries a licence that restricts feeding the content
to a model or running the result inside a business. That removes both a legal
question and a dependency on somebody else's approval, which matters more than
the extra data wrangling.

The Wikidata Query Service is frequently degraded and rate-limits hard, so the
design keeps SPARQL to a handful of small queries and does all enrichment
through the much steadier REST APIs. Every stage caches to data/_cache, so a
failure in stage 3 never costs you stage 1.

Usage:
    python -m ingest.wikidata films      # SPARQL + REST + Wikipedia -> JSONL
    python -m ingest.wikidata series
    python -m ingest.wikidata talent     # derived from films
    python -m ingest.wikidata embed      # Gemini embeddings for both tables
    python -m ingest.wikidata load       # push into ClickHouse
    python -m ingest.wikidata all
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional

try:  # corporate TLS interception on the dev machine
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

from app.config import settings
from app.cost import CostMeter
from app.store import schema

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ingest.wikidata")

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = DATA / "_cache"
DATA.mkdir(exist_ok=True)
CACHE.mkdir(exist_ok=True)

UA = "GreenlightStudio/0.1 (https://github.com/studioso-tech/greenlight-studio) research"
SPARQL_EP = "https://query.wikidata.org/sparql"
WD_API = "https://www.wikidata.org/w/api.php"
WP_API = "https://en.wikipedia.org/w/api.php"

USD = "wd:Q4917"
FILM = "wd:Q11424"
TV_SERIES = "wd:Q5398426"

# WDQS is degraded often enough that this is not paranoia.
SPARQL_COOLDOWN = 8.0
SPARQL_RETRIES = 4


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------

def _get_json(url: str, timeout: int = 240) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def sparql(query: str, label: str = "") -> list[dict]:
    """Run a SPARQL query, tolerating the query service's bad days."""
    url = SPARQL_EP + "?" + urllib.parse.urlencode({"query": query})
    delay = SPARQL_COOLDOWN
    for attempt in range(1, SPARQL_RETRIES + 1):
        try:
            started = time.perf_counter()
            data = _get_json(url)
            rows = data["results"]["bindings"]
            logger.info("sparql %s -> %d rows in %.1fs", label, len(rows), time.perf_counter() - started)
            return rows
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "code", None)
            if attempt == SPARQL_RETRIES:
                raise
            wait = 65.0 if status == 429 else delay
            logger.warning("sparql %s attempt %d failed (%s); retrying in %.0fs",
                           label, attempt, type(exc).__name__, wait)
            time.sleep(wait)
            delay *= 2
    return []


def qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _value(row: dict, key: str) -> Optional[str]:
    entry = row.get(key)
    return entry["value"] if entry else None


# --------------------------------------------------------------------------
# stage 1: the numeric spine, from SPARQL
# --------------------------------------------------------------------------

FILM_CORE_QUERY = f"""
SELECT ?film (MIN(?d) AS ?released) (MAX(?b) AS ?budget) (MAX(?x) AS ?box) WHERE {{
  ?film p:P2130/psv:P2130 [ wikibase:quantityAmount ?b ; wikibase:quantityUnit {USD} ] ;
        p:P2142/psv:P2142 [ wikibase:quantityAmount ?x ; wikibase:quantityUnit {USD} ] ;
        wdt:P31 {FILM} ;
        wdt:P577 ?d .
}}
GROUP BY ?film
"""

SERIES_CORE_QUERY = f"""
SELECT ?series (MAX(?s) AS ?seasons) (MAX(?e) AS ?episodes)
       (MIN(?start) AS ?started) (MAX(?end) AS ?ended) WHERE {{
  ?series wdt:P31 {TV_SERIES} ; wdt:P2437 ?s .
  ?article schema:about ?series ; schema:isPartOf <https://en.wikipedia.org/> .
  OPTIONAL {{ ?series wdt:P1113 ?e }}
  OPTIONAL {{ ?series wdt:P580 ?start }}
  OPTIONAL {{ ?series wdt:P582 ?end }}
}}
GROUP BY ?series
"""

SCORES_QUERY = f"""
SELECT ?film ?score ?by WHERE {{
  ?film p:P2130/psv:P2130 [ wikibase:quantityAmount ?b ; wikibase:quantityUnit {USD} ] ;
        p:P2142/psv:P2142 [ wikibase:quantityAmount ?x ; wikibase:quantityUnit {USD} ] .
  ?film p:P444 ?statement .
  ?statement ps:P444 ?score .
  OPTIONAL {{ ?statement pq:P447 ?by }}
}}
"""


def _cache(name: str, build) -> Any:
    path = CACHE / f"{name}.json"
    if path.exists():
        logger.info("cache hit: %s", path.name)
        return json.loads(path.read_text(encoding="utf-8"))
    value = build()
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    logger.info("cached %s (%.1f MB)", path.name, path.stat().st_size / 1e6)
    return value


# --------------------------------------------------------------------------
# review scores: "91%", "8/10", "35/100" -> 0..100
# --------------------------------------------------------------------------

_PCT = re.compile(r"^\s*([\d.]+)\s*%\s*$")
_FRACTION = re.compile(r"^\s*([\d.]+)\s*/\s*([\d.]+)\s*$")


def normalise_score(raw: str) -> Optional[float]:
    if not raw:
        return None
    match = _PCT.match(raw)
    if match:
        value = float(match.group(1))
        return value if 0 <= value <= 100 else None
    match = _FRACTION.match(raw)
    if match:
        numerator, denominator = float(match.group(1)), float(match.group(2))
        if denominator > 0:
            value = 100 * numerator / denominator
            return value if 0 <= value <= 100 else None
    return None


# --------------------------------------------------------------------------
# stage 2: labels, genres, language, credits - from the REST API
# --------------------------------------------------------------------------

GENRE_KEYWORDS = [
    ("science fiction", "Science Fiction"), ("sci-fi", "Science Fiction"),
    ("superhero", "Action"), ("action", "Action"), ("martial arts", "Action"),
    ("adventure", "Adventure"), ("animated", "Animation"), ("animation", "Animation"),
    ("anime", "Animation"), ("comedy", "Comedy"), ("crime", "Crime"), ("heist", "Crime"),
    ("documentary", "Documentary"), ("drama", "Drama"), ("family", "Family"),
    ("children", "Family"), ("fantasy", "Fantasy"), ("historical", "History"),
    ("history", "History"), ("biographical", "History"), ("horror", "Horror"),
    ("slasher", "Horror"), ("musical", "Music"), ("music", "Music"),
    ("mystery", "Mystery"), ("detective", "Mystery"), ("noir", "Mystery"),
    ("romantic", "Romance"), ("romance", "Romance"), ("thriller", "Thriller"),
    ("spy", "Thriller"), ("disaster", "Thriller"), ("war", "War"), ("western", "Western"),
]


def normalise_genre(label: str) -> list[str]:
    """All canonical genres a Wikidata label implies.

    A romantic comedy is both, and collapsing it to whichever keyword happens to
    be listed first loses half the signal the comparable search runs on.
    """
    lowered = label.lower()
    found: list[str] = []
    for keyword, canonical in GENRE_KEYWORDS:
        if keyword in lowered and canonical not in found:
            found.append(canonical)
    return found


def fetch_entities(ids: list[str], props: str = "labels|claims|sitelinks") -> dict[str, dict]:
    """wbgetentities, 50 ids per call. Far steadier than the query service."""
    out: dict[str, dict] = {}
    for start in range(0, len(ids), 50):
        chunk = ids[start : start + 50]
        params = {
            "action": "wbgetentities", "ids": "|".join(chunk), "props": props,
            "languages": "en|ja", "sitefilter": "enwiki", "format": "json",
        }
        for attempt in range(4):
            try:
                data = _get_json(WD_API + "?" + urllib.parse.urlencode(params), timeout=120)
                out.update(data.get("entities", {}))
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    logger.warning("wbgetentities failed for %d ids: %s", len(chunk), exc)
                else:
                    time.sleep(3 * (attempt + 1))
        if (start // 50) % 10 == 0:
            logger.info("entities %d/%d", min(start + 50, len(ids)), len(ids))
    return out


def _claim_ids(entity: dict, prop: str) -> list[str]:
    values = []
    for claim in entity.get("claims", {}).get(prop, []):
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        data = snak.get("datavalue", {}).get("value", {})
        if isinstance(data, dict) and "id" in data:
            values.append(data["id"])
    return values


def _claim_amount(entity: dict, prop: str) -> Optional[float]:
    for claim in entity.get("claims", {}).get(prop, []):
        snak = claim.get("mainsnak", {})
        data = snak.get("datavalue", {}).get("value", {})
        if isinstance(data, dict) and "amount" in data:
            try:
                return float(data["amount"])
            except ValueError:
                return None
    return None


def _label(entity: dict, lang: str = "en") -> str:
    return (entity.get("labels", {}).get(lang, {}) or {}).get("value", "")


def _enwiki_title(entity: dict) -> str:
    return (entity.get("sitelinks", {}).get("enwiki", {}) or {}).get("title", "")


# --------------------------------------------------------------------------
# stage 3: synopsis text from Wikipedia
# --------------------------------------------------------------------------

def fetch_extracts(titles: list[str]) -> dict[str, str]:
    """Intro extract per article, 20 titles per request."""
    out: dict[str, str] = {}
    unique = [t for t in dict.fromkeys(titles) if t]
    for start in range(0, len(unique), 20):
        chunk = unique[start : start + 20]
        params = {
            "action": "query", "prop": "extracts", "exintro": "1", "explaintext": "1",
            "redirects": "1", "titles": "|".join(chunk), "format": "json", "formatversion": "2",
        }
        for attempt in range(4):
            try:
                data = _get_json(WP_API + "?" + urllib.parse.urlencode(params), timeout=120)
                for page in data.get("query", {}).get("pages", []):
                    text = (page.get("extract") or "").strip()
                    if text:
                        out[page["title"]] = text[:2000]
                # follow redirects back to the title we asked for
                for redirect in data.get("query", {}).get("redirects", []):
                    if redirect["to"] in out:
                        out[redirect["from"]] = out[redirect["to"]]
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    logger.warning("extracts failed for %d titles: %s", len(chunk), exc)
                else:
                    time.sleep(3 * (attempt + 1))
        if (start // 20) % 25 == 0:
            logger.info("extracts %d/%d", min(start + 20, len(unique)), len(unique))
    return out


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

def _iso_date(raw: Optional[str]) -> Optional[str]:
    if not raw or len(raw) < 10:
        return None
    day = raw.lstrip("+")[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        return None
    if day[5:7] == "00" or day[8:10] == "00":  # Wikidata year-only precision
        day = f"{day[:4]}-01-01"
    if not (1900 <= int(day[:4]) <= 2100):
        return None
    return day


def build_films() -> None:
    core = _cache("film_core", lambda: sparql(FILM_CORE_QUERY, "film core"))
    scores_raw = _cache("film_scores", lambda: sparql(SCORES_QUERY, "film scores"))
    logger.info("core films: %d", len(core))

    scores: dict[str, list[float]] = defaultdict(list)
    for row in scores_raw:
        value = normalise_score(_value(row, "score") or "")
        if value is not None:
            scores[qid(_value(row, "film") or "")].append(value)

    ids = [qid(_value(row, "film") or "") for row in core]
    entities = _cache("film_entities", lambda: fetch_entities(ids))

    # Resolve the QIDs referenced by genre / language / country / credits.
    referenced: set[str] = set()
    for entity in entities.values():
        for prop in ("P136", "P364", "P495", "P57", "P161"):
            referenced.update(_claim_ids(entity, prop)[:6])
    lookup = _cache("film_refs", lambda: fetch_entities(sorted(referenced), props="labels"))

    def label_of(entity_id: str) -> str:
        return _label(lookup.get(entity_id, {}))

    titles = [_enwiki_title(entities.get(i, {})) for i in ids]
    extracts = _cache("film_extracts", lambda: fetch_extracts(titles))

    rows: list[dict] = []
    credits: dict[str, dict] = {}
    for core_row in core:
        entity_id = qid(_value(core_row, "film") or "")
        entity = entities.get(entity_id)
        if not entity:
            continue
        released = _iso_date(_value(core_row, "released"))
        title = _label(entity)
        if not released or not title:
            continue
        try:
            budget = int(float(_value(core_row, "budget") or 0))
            revenue = int(float(_value(core_row, "box") or 0))
        except ValueError:
            continue
        if budget < 100_000 or revenue < 100_000:
            continue  # placeholder values, not real production budgets

        genres: list[str] = []
        for genre_id in _claim_ids(entity, "P136")[:6]:
            for canonical in normalise_genre(label_of(genre_id)):
                if canonical not in genres:
                    genres.append(canonical)
        if not genres:
            genres = ["Drama"]

        wiki_title = _enwiki_title(entity)
        synopsis = extracts.get(wiki_title, "")
        score_values = scores.get(entity_id, [])
        roi = revenue / budget

        rows.append({
            "wikidata_id": entity_id,
            "title": title,
            "title_ja": _label(entity, "ja"),
            "original_language": (label_of(_claim_ids(entity, "P364")[0])
                                  if _claim_ids(entity, "P364") else ""),
            "origin_country": [label_of(c) for c in _claim_ids(entity, "P495")[:3]],
            "release_date": released,
            "release_year": int(released[:4]),
            "release_month": int(released[5:7]),
            "genres": genres,
            "runtime_min": int(_claim_amount(entity, "P2047") or 0),
            "budget_usd": budget,
            "revenue_usd": revenue,
            "roi_multiple": round(roi, 4),
            "profitable": 1 if roi >= 2.5 else 0,
            "audience_score": round(statistics.fmean(score_values), 1) if score_values else 0.0,
            "has_audience_score": 1 if score_values else 0,
            "synopsis": synopsis,
        })
        credits[entity_id] = {
            "directors": [{"id": p, "name": label_of(p)} for p in _claim_ids(entity, "P57")[:2]],
            "cast": [{"id": p, "name": label_of(p)} for p in _claim_ids(entity, "P161")[:4]],
        }

    logger.info("assembled %d films (%d with a synopsis, %d with a score)",
                len(rows), sum(1 for r in rows if r["synopsis"]),
                sum(r["has_audience_score"] for r in rows))
    write_jsonl(schema.MOVIES, rows)
    write_json("film_credits", credits)
    write_manifest("film", rows, len(core))


def build_series() -> None:
    core = _cache("series_core", lambda: sparql(SERIES_CORE_QUERY, "series core"))
    logger.info("core series: %d", len(core))

    ids = [qid(_value(row, "series") or "") for row in core]
    entities = _cache("series_entities", lambda: fetch_entities(ids))

    referenced: set[str] = set()
    for entity in entities.values():
        for prop in ("P136", "P364", "P495", "P449"):
            referenced.update(_claim_ids(entity, prop)[:6])
    lookup = _cache("series_refs", lambda: fetch_entities(sorted(referenced), props="labels"))

    def label_of(entity_id: str) -> str:
        return _label(lookup.get(entity_id, {}))

    titles = [_enwiki_title(entities.get(i, {})) for i in ids]
    extracts = _cache("series_extracts", lambda: fetch_extracts(titles))

    rows: list[dict] = []
    for core_row in core:
        entity_id = qid(_value(core_row, "series") or "")
        entity = entities.get(entity_id)
        if not entity:
            continue
        title = _label(entity)
        started = _iso_date(_value(core_row, "started"))
        if not title or not started:
            continue
        try:
            seasons = int(float(_value(core_row, "seasons") or 0))
            episodes = int(float(_value(core_row, "episodes") or 0))
        except ValueError:
            continue
        if seasons <= 0 or seasons > 100:
            continue

        ended_on = _iso_date(_value(core_row, "ended"))
        has_ended = 1 if ended_on else 0

        genres: list[str] = []
        for genre_id in _claim_ids(entity, "P136")[:6]:
            for canonical in normalise_genre(label_of(genre_id)):
                if canonical not in genres:
                    genres.append(canonical)
        if not genres:
            genres = ["Drama"]

        rows.append({
            "wikidata_id": entity_id,
            "title": title,
            "title_ja": _label(entity, "ja"),
            "original_language": (label_of(_claim_ids(entity, "P364")[0])
                                  if _claim_ids(entity, "P364") else ""),
            "origin_country": [label_of(c) for c in _claim_ids(entity, "P495")[:3]],
            "first_air_date": started,
            "last_air_date": ended_on or started,
            "first_air_year": int(started[:4]),
            "number_of_seasons": seasons,
            "number_of_episodes": episodes,
            "genres": genres,
            "networks": [label_of(n) for n in _claim_ids(entity, "P449")[:3]],
            "audience_score": 0.0,
            "has_audience_score": 0,
            "synopsis": extracts.get(_enwiki_title(entity), ""),
            "returned_after_s1": 1 if seasons >= 2 else 0,
            "has_ended": has_ended,
            "did_not_return": 1 if (seasons == 1 and has_ended) else 0,
            "still_running": 0 if has_ended else 1,
        })

    logger.info("assembled %d series (%d with a synopsis, %d returned after S1, %d did not)",
                len(rows), sum(1 for r in rows if r["synopsis"]),
                sum(r["returned_after_s1"] for r in rows),
                sum(r["did_not_return"] for r in rows))
    write_jsonl(schema.SERIES, rows)
    write_manifest("series", rows, len(core))


def build_talent() -> None:
    films = read_jsonl(schema.MOVIES)
    credits = read_json("film_credits")
    if not films or not credits:
        raise SystemExit("Run 'films' first: talent is derived from the film table.")

    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    names: dict[str, str] = {}
    for film in films:
        entry = credits.get(film["wikidata_id"])
        if not entry:
            continue
        genre = film["genres"][0]
        for role, people in (("director", entry.get("directors", [])),
                             ("actor", entry.get("cast", []))):
            for person in people:
                if not person.get("name"):
                    continue
                grouped[(person["id"], role, genre)].append(film)
                names[person["id"]] = person["name"]

    rows = []
    for (person_id, role, genre), credited in grouped.items():
        if len(credited) < 2:
            continue
        rois = [f["roi_multiple"] for f in credited]
        rows.append({
            "wikidata_id": person_id,
            "name": names[person_id],
            "role_type": role,
            "primary_genre": genre,
            "credits": len(credited),
            "avg_roi_multiple": round(statistics.fmean(rois), 4),
            "median_roi_multiple": round(statistics.median(rois), 4),
            "avg_revenue_usd": int(statistics.fmean(f["revenue_usd"] for f in credited)),
            "hit_rate_pct": round(100 * sum(1 for r in rois if r >= 2.5) / len(rois), 1),
        })
    logger.info("built %d talent rows with 2+ credits in one genre", len(rows))
    write_jsonl(schema.TALENT, rows)


# --------------------------------------------------------------------------
# embeddings and load
# --------------------------------------------------------------------------

def embed_all() -> None:
    from app.embeddings import embed_texts

    for table, title_key in ((schema.MOVIES, "title"), (schema.SERIES, "title")):
        rows = read_jsonl(table)
        if not rows:
            logger.warning("%s: nothing to embed", table)
            continue
        pending = [r for r in rows if not r.get("embedding")]
        if not pending:
            logger.info("%s: embeddings already present", table)
            continue
        meter = CostMeter(f"embed-{table}", ceiling_usd=float("inf"))
        texts = [f"{r[title_key]}. {r.get('synopsis') or ' '.join(r['genres'])}"[:4000]
                 for r in pending]
        logger.info("%s: embedding %d rows", table, len(pending))
        vectors = embed_texts(texts, meter=meter)
        for row, vector in zip(pending, vectors):
            row["embedding"] = vector
        write_jsonl(table, rows)
        logger.info("%s: %s", table, meter.summary())


def load_into_clickhouse() -> None:
    from app.store.clickhouse import ClickHouseStore

    if not settings().clickhouse_configured:
        raise SystemExit("CLICKHOUSE_HOST is not set.")
    store = ClickHouseStore()
    store.create_schema()
    for table in (schema.MOVIES, schema.SERIES, schema.TALENT):
        rows = read_jsonl(table)
        if not rows:
            logger.warning("%s: nothing to load", table)
            continue
        missing = [r for r in rows if table != schema.TALENT and not r.get("embedding")]
        if missing:
            raise SystemExit(f"{table}: {len(missing)} rows have no embedding. Run 'embed' first.")
        # ClickHouse Date columns want real date objects, not ISO strings.
        for row in rows:
            for column in ("release_date", "first_air_date", "last_air_date"):
                value = row.get(column)
                if isinstance(value, str):
                    row[column] = date.fromisoformat(value)
        inserted = 0
        for start in range(0, len(rows), 500):
            inserted += store.insert_rows(table, rows[start : start + 500])
            logger.info("%s: %d/%d", table, inserted, len(rows))
    logger.info("counts now: %s", store.table_counts())


# --------------------------------------------------------------------------
# small io helpers
# --------------------------------------------------------------------------

def write_jsonl(name: str, rows: list[dict]) -> None:
    path = DATA / f"{name}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    logger.info("wrote %s (%d rows, %.1f MB)", path.name, len(rows), path.stat().st_size / 1e6)


def read_jsonl(name: str) -> list[dict]:
    path = DATA / f"{name}.jsonl"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_json(name: str, obj: Any) -> None:
    (DATA / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def read_json(name: str) -> Any:
    path = DATA / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_manifest(mode: str, rows: list[dict], candidates: int) -> None:
    """What the README and the UI are allowed to claim. Nothing rounder."""
    manifest = read_json("manifest") or {}
    year_key = "release_year" if mode == "film" else "first_air_year"
    years = [r[year_key] for r in rows if r.get(year_key)]
    languages = sorted({r["original_language"] for r in rows if r["original_language"]})
    manifest[mode] = {
        "rows": len(rows),
        "candidates_examined": candidates,
        "with_synopsis": sum(1 for r in rows if r.get("synopsis")),
        "first_year": min(years) if years else None,
        "last_year": max(years) if years else None,
        "years_covered": (max(years) - min(years) + 1) if years else 0,
        "languages": len(languages),
        "built_on": date.today().isoformat(),
        "sources": ["Wikidata (CC0)", "English Wikipedia (CC BY-SA 4.0)"],
    }
    write_json("manifest", manifest)
    logger.info("manifest[%s] = %s", mode, manifest[mode])


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Greenlight catalogue from Wikidata.")
    parser.add_argument("stage", choices=["films", "series", "talent", "embed", "load", "all"])
    args = parser.parse_args()

    if args.stage in ("films", "all"):
        build_films()
    if args.stage in ("series", "all"):
        build_series()
    if args.stage in ("talent", "all"):
        build_talent()
    if args.stage in ("embed", "all"):
        embed_all()
    if args.stage in ("load", "all"):
        load_into_clickhouse()


if __name__ == "__main__":
    main()
