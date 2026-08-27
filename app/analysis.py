"""The orchestrator: one request, three agents, deterministic maths in between.

This is the only place that knows the whole shape of an analysis. The API layer
calls run_analysis(); the What-if endpoint calls recompute(), which reuses the
evidence already gathered and never touches a model.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from app.agents import pipeline
from app.agents.context import RequestContext, reset_context, set_context
from app.agents.guardrails import Limits, RunawayGuard, with_deadline
from app.config import settings
from app.cost import BudgetExceeded, CostMeter
from app.embeddings import embed_one
from app.scoring import (
    FilmProjection,
    budget_band,
    SeriesProjection,
    project_film,
    project_series,
    score_film,
    score_series,
)

logger = logging.getLogger("greenlight.analysis")

Mode = Literal["film", "series"]


@dataclass
class Proposal:
    """What the reviewer is putting on the table."""

    mode: Mode = "film"
    budget_usd: int = 40_000_000            # film
    per_episode_budget_usd: int = 3_000_000  # series
    episodes: int = 8                        # series
    release_month: int = 0                   # 0 = unspecified
    locale: str = "en"

    def describe(self) -> str:
        if self.mode == "film":
            line = f"a theatrical feature at a production budget of ${self.budget_usd:,}"
        else:
            total = self.per_episode_budget_usd * self.episodes
            line = (f"a {self.episodes}-episode season at ${self.per_episode_budget_usd:,} "
                    f"per episode (${total:,} for the season)")
        if self.release_month:
            months = ["", "January", "February", "March", "April", "May", "June", "July",
                      "August", "September", "October", "November", "December"]
            line += f", targeting a {months[self.release_month]} release"
        return line


def _project_and_score(proposal: Proposal, comps: list[dict], benchmark: dict):
    """Deterministic half. No model call reaches this function."""
    if proposal.mode == "film":
        projection = project_film(proposal.budget_usd, comps, benchmark or None)
        score = score_film(projection, comps, proposal.budget_usd, benchmark or None)
    else:
        projection = project_series(
            proposal.per_episode_budget_usd, proposal.episodes, comps, benchmark or None
        )
        score = score_series(projection, comps, benchmark or None)
    return projection, score


def _numbers(projection, score) -> dict:
    return {"projection": projection.to_dict(), "score": score.to_dict()}


def _band_benchmark(proposal: Proposal, brief) -> dict:
    """Outcomes for this genre at this budget. The heart of the What-if."""
    from app.store import queries

    genre = getattr(brief, "primary_genre", None)
    language = getattr(brief, "original_language", None)
    if proposal.mode != "film":
        # Language matters more than genre for television: it decides whether
        # "did not return" means cancelled or simply finished. Fall back to
        # genre alone if the market slice is too thin to be a baseline.
        payload = queries.genre_benchmark(mode="series", genre=genre, language=language)
        rows = payload.get("rows") or []
        if not rows or (rows[0].get("sample_size") or 0) < 12:
            payload = queries.genre_benchmark(mode="series", genre=genre)
    else:
        payload = queries.genre_benchmark(
            mode="film", genre=genre, budget_band_usd=budget_band(proposal.budget_usd)
        )
    rows = payload.get("rows") or []
    row = dict(rows[0]) if rows else {}
    row["_elapsed_ms"] = payload.get("elapsed_ms")
    return row


def _evidence(ctx: RequestContext) -> dict:
    """Only the fields a memo may quote. Embeddings and full synopses stay out."""
    keep_film = ("wikidata_id", "title", "title_ja", "release_year", "genres", "original_language",
                 "budget_usd", "revenue_usd", "roi_multiple", "audience_score",
                 "has_audience_score", "tone_distance")
    keep_series = ("wikidata_id", "title", "title_ja", "release_year", "genres", "original_language",
                   "number_of_seasons", "number_of_episodes", "networks",
                   "returned_after_s1", "did_not_return", "still_running", "tone_distance")
    keep = keep_film if ctx.mode == "film" else keep_series
    return {
        "comparable_titles": [{k: c[k] for k in keep if k in c} for c in ctx.comps],
        "segment_benchmark": ctx.benchmark,
        "talent": ctx.talent[:5],
    }


async def run_analysis(material: str, proposal: Proposal) -> dict:
    """Full pass: read the material, investigate, compute, write the memo."""
    request_id = str(uuid.uuid4())[:8]
    meter = CostMeter(request_id)
    ctx = RequestContext(
        request_id=request_id, mode=proposal.mode, meter=meter, locale=proposal.locale
    )
    guard = RunawayGuard(ctx)
    token = set_context(ctx)
    started = time.perf_counter()
    warnings: list[str] = []

    try:
        async def _work() -> dict:
            brief = await pipeline.extract_brief(material, ctx, guard)
            ctx.embedding = embed_one(pipeline.embedding_text(brief), meter=meter)

            findings = await pipeline.research(brief, proposal.describe(), ctx, guard)

            if not ctx.comps:
                # The analyst never got a usable comp set; fall back to an
                # unfiltered nearest-neighbour pull so the request still
                # produces something defensible, and say so.
                from app.store import queries

                warnings.append(
                    "The analyst did not retrieve a comparable set, so an unfiltered "
                    "nearest-neighbour search was used instead."
                )
                payload = queries.find_comparable_titles(
                    embedding=ctx.embedding, mode=ctx.mode, limit=8
                )
                ctx.comps = payload.get("rows") or []

            # The analyst may or may not have benchmarked the right budget band.
            # The projection depends on it, so make sure we have one.
            benchmark = _band_benchmark(proposal, brief) or ctx.benchmark
            projection, score = _project_and_score(proposal, ctx.comps, benchmark)
            ctx.benchmark = benchmark
            numbers = _numbers(projection, score)
            evidence = _evidence(ctx)

            memo = await pipeline.write_memo(
                brief, proposal.describe(), findings, numbers, evidence, ctx, guard
            )
            return {
                "brief": brief.model_dump(),
                "findings": findings,
                "memo": memo,
                **numbers,
                "evidence": evidence,
            }

        result = await with_deadline(_work(), settings().agent_timeout_sec + 30, request_id)

    except BudgetExceeded as exc:
        logger.error("request %s stopped on budget: %s", request_id, exc)
        raise
    finally:
        reset_context(token)

    result.update({
        "request_id": request_id,
        "mode": proposal.mode,
        "locale": proposal.locale,
        "proposal": proposal.__dict__,
        "warnings": warnings,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "cost": meter.summary(),
        "guardrails": guard.status(),
        "trace": [t.__dict__ for t in ctx.trace],
        "clickhouse_ms": ctx.clickhouse_ms,
    })
    logger.info(
        "request %s done in %.1fs, %d tool calls, $%.4f",
        request_id, result["elapsed_seconds"], guard.tool_calls, meter.usd,
    )
    return result


# The one lever a committee actually pulls, and the steps worth showing on it.
# Multiples of what was proposed, so the ladder is meaningful at any scale.
_FILM_STEPS = (0.25, 0.4, 0.6, 0.8, 1.0, 1.3, 1.8, 2.5)
_EPISODE_STEPS = (6, 8, 10, 12, 16, 22)


def lever_ladder(previous: dict, proposal: Proposal,
                 excluded_ids: Optional[list[str]] = None) -> dict:
    """Where along the lever the verdict actually changes.

    A memo that says "reduce the budget by five to ten million" and a slider
    that lets you try it are the same thought in two places, and neither is
    quite an answer. This computes the answer: the point at which this project
    stops being a Reshape and starts being something else.

    Every step is the same deterministic model, re-read from ClickHouse. It
    costs one query per rung and no model call at all.
    """
    from types import SimpleNamespace

    started = time.perf_counter()
    all_comps = previous.get("evidence", {}).get("comparable_titles", [])
    rejected = set(excluded_ids or [])
    comps = [c for c in all_comps if c.get("wikidata_id") not in rejected]
    if not comps:
        raise ValueError("Nothing to evaluate: every comparable title was excluded.")

    brief = SimpleNamespace(**(previous.get("brief") or {}))
    current_verdict = None
    rungs: list[dict] = []
    db_ms = 0.0

    if proposal.mode == "film":
        base = proposal.budget_usd
        candidates = [(int(base * m), m) for m in _FILM_STEPS]
    else:
        base = proposal.episodes
        candidates = [(n, n / base if base else 1.0) for n in _EPISODE_STEPS]

    for value, multiple in candidates:
        step = Proposal(**{**proposal.__dict__})
        if proposal.mode == "film":
            step.budget_usd = value
        else:
            step.episodes = value
        benchmark = _band_benchmark(step, brief)
        db_ms += float(benchmark.get("_elapsed_ms") or 0.0)
        try:
            projection, score = _project_and_score(step, comps, benchmark)
        except ValueError:
            continue
        is_current = abs(multiple - 1.0) < 1e-9
        if is_current:
            current_verdict = score.verdict
        rungs.append({
            "value": value,
            "multiple": round(multiple, 2),
            "is_current": is_current,
            "score": score.value,
            "verdict": score.verdict,
            "sample_size": benchmark.get("sample_size"),
            "probability_pct": (projection.probability_break_even_pct
                                if proposal.mode == "film"
                                else projection.renewal_probability_pct),
        })

    # The nearest rung in each direction whose verdict differs from today's.
    tipping = None
    if current_verdict:
        current_index = next((i for i, r in enumerate(rungs) if r["is_current"]), None)
        if current_index is not None:
            for offset in range(1, len(rungs)):
                for index in (current_index - offset, current_index + offset):
                    if 0 <= index < len(rungs) and rungs[index]["verdict"] != current_verdict:
                        tipping = rungs[index]
                        break
                if tipping:
                    break

    return {
        "mode": proposal.mode,
        "lever": "budget_usd" if proposal.mode == "film" else "episodes",
        "current_verdict": current_verdict,
        "rungs": rungs,
        "tipping_point": tipping,
        "comparables_used": len(comps),
        "clickhouse_ms": round(db_ms, 2),
        "total_ms": round((time.perf_counter() - started) * 1000, 2),
        "model_calls": 0,
    }


def recompute(previous: dict, proposal: Proposal,
              excluded_ids: Optional[list[str]] = None) -> dict:
    """What-if: move a lever or reject a comparable, then re-derive the verdict.

    One ClickHouse query, no model call. Changing the budget changes which
    films the projection is compared against, so the band has to be re-read -
    that is the whole point, and it is also the moment the database's speed is
    visible to a human hand on a slider.

    excluded_ids is the argument a committee actually makes. "That title is not
    comparable" is the most common objection in the room, and a tool that
    cannot accept it is asking to be believed rather than used.
    """
    from types import SimpleNamespace

    started = time.perf_counter()
    all_comps = previous.get("evidence", {}).get("comparable_titles", [])
    if not all_comps:
        raise ValueError("Nothing to recompute: the original analysis has no comparable set.")

    rejected = set(excluded_ids or [])
    comps = [c for c in all_comps if c.get("wikidata_id") not in rejected]
    if not comps:
        raise ValueError(
            "Every comparable title was excluded. Keep at least one, or run a new analysis."
        )

    brief = SimpleNamespace(**(previous.get("brief") or {}))
    benchmark = _band_benchmark(proposal, brief)
    db_ms = benchmark.get("_elapsed_ms") or 0.0
    if not benchmark.get("sample_size"):
        benchmark = previous.get("evidence", {}).get("segment_benchmark", {})

    projection, score = _project_and_score(proposal, comps, benchmark)
    return {
        "request_id": previous.get("request_id"),
        "mode": proposal.mode,
        "proposal": proposal.__dict__,
        **_numbers(projection, score),
        "budget_band_usd": budget_band(proposal.budget_usd) if proposal.mode == "film" else None,
        "band_sample_size": benchmark.get("sample_size"),
        "comparables_used": len(comps),
        "comparables_excluded": len(all_comps) - len(comps),
        "excluded_ids": sorted(rejected),
        "clickhouse_ms": round(float(db_ms), 2),
        "total_ms": round((time.perf_counter() - started) * 1000, 2),
        "model_calls": 0,
    }
