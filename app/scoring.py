"""Deterministic financial model.

The language model decides what to look up and how to explain it. It never does
the arithmetic - every number below is computed in Python from rows that came
out of ClickHouse, so the same inputs always produce the same figures and the
What-if sliders can re-run in milliseconds without another model call.

Assumptions are stated explicitly and returned with the result, because a
greenlight memo that hides its assumptions is worthless.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

Mode = Literal["film", "series"]

# A theatrical release returns roughly half of box office to the distributor,
# and prints & advertising typically runs about half of negative cost. So a
# feature needs about 2.5x its production budget in worldwide gross to break
# even. This is the standard industry heuristic, not a precise accounting.
DISTRIBUTOR_SHARE = 0.50
PA_SPEND_RATIO = 0.50
BREAK_EVEN_MULTIPLE = (1 + PA_SPEND_RATIO) / DISTRIBUTOR_SHARE  # 3.0... see below

# The rule of thumb is usually quoted as 2.5x rather than 3.0x because
# ancillary revenue (streaming, TV, home video) covers part of the gap.
ANCILLARY_OFFSET = 0.5
THEATRICAL_BREAK_EVEN_MULTIPLE = BREAK_EVEN_MULTIPLE - ANCILLARY_OFFSET  # 2.5


@dataclass
class Assumption:
    key: str
    value: Any
    note: str


@dataclass
class FilmProjection:
    budget_usd: int
    break_even_gross_usd: int
    bear_gross_usd: int
    base_gross_usd: int
    bull_gross_usd: int
    bear_roi: float
    base_roi: float
    bull_roi: float
    probability_break_even_pct: float
    probability_hit_pct: float
    comp_sample_size: int
    assumptions: list[Assumption] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "assumptions"}
        d["assumptions"] = [a.__dict__ for a in self.assumptions]
        return d


@dataclass
class SeriesProjection:
    per_episode_budget_usd: int
    episodes: int
    season_cost_usd: int
    renewal_probability_pct: float
    cancellation_risk_pct: float
    expected_seasons: float
    reach_season_three_pct: float
    comp_sample_size: int
    assumptions: list[Assumption] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "assumptions"}
        d["assumptions"] = [a.__dict__ for a in self.assumptions]
        return d


def _roi_values(comps: list[dict]) -> list[float]:
    return [float(c["roi_multiple"]) for c in comps if c.get("roi_multiple") is not None]


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def budget_band(budget_usd: int) -> list[int]:
    """The band of comparable budgets around a proposal.

    Plus or minus 40%, which is roughly the range within which two films are
    financed and sold the same way. Narrow enough to mean something, wide enough
    to still contain a usable sample.
    """
    return [int(budget_usd * 0.6), int(budget_usd * 1.4)]


def project_film(
    budget_usd: int,
    comps: list[dict],
    benchmark: Optional[dict] = None,
) -> FilmProjection:
    """Scenario band for a film at this budget.

    Two sources, deliberately combined:

      the budget band - how films of this genre AT THIS BUDGET actually did.
        This is what makes the budget matter. A $25m film and a $200m film face
        different odds of the same multiple, and only a budget-matched sample
        shows that.

      the tone comparables - how films that resemble THIS ONE actually did.
        This is what makes the subject matter, and it is the half a generic
        genre average throws away.

    Neither alone is honest. The band ignores what the film is about; the comps
    ignore what it costs.
    """
    comp_roi = _roi_values(comps)
    band_roi: list[float] = []
    band_sample = 0
    if benchmark:
        band_sample = int(benchmark.get("sample_size") or 0)
        for key in ("p25_roi_multiple", "median_roi_multiple", "p75_roi_multiple"):
            value = benchmark.get(key)
            if value is not None:
                band_roi.append(float(value))

    if not comp_roi and not band_roi:
        raise ValueError("No ROI evidence available: cannot project without comparables.")

    def blended(q: float) -> float:
        parts, weights = [], []
        if comp_roi:
            parts.append(_quantile(comp_roi, q))
            weights.append(0.5 if band_roi and band_sample >= 8 else 1.0)
        if band_roi and band_sample >= 8:
            parts.append(_quantile(band_roi, q))
            weights.append(0.5 if comp_roi else 1.0)
        elif band_roi and not comp_roi:
            parts.append(_quantile(band_roi, q))
            weights.append(1.0)
        total = sum(weights) or 1.0
        return sum(p * w for p, w in zip(parts, weights)) / total

    bear, base, bull = blended(0.20), blended(0.50), blended(0.85)
    break_even_gross = int(budget_usd * THEATRICAL_BREAK_EVEN_MULTIPLE)

    # Probability comes from the budget-matched band when there is one, because
    # "how often does a film like this at this price recoup" is a question about
    # the price as much as the film.
    band_hit_rate = benchmark.get("pct_hit") if benchmark else None
    if band_hit_rate is not None and band_sample >= 8:
        break_even_pct = float(band_hit_rate)
        hit_pct = float(benchmark.get("pct_recouped_budget") or 0.0)
        sample = band_sample
        source = f"{band_sample} films of this genre in this budget band"
    else:
        recouped = sum(1 for r in comp_roi if r >= THEATRICAL_BREAK_EVEN_MULTIPLE)
        break_even_pct = round(100 * recouped / len(comp_roi), 1) if comp_roi else 0.0
        hit_pct = round(100 * sum(1 for r in comp_roi if r >= 4.0) / len(comp_roi), 1) if comp_roi else 0.0
        sample = len(comp_roi)
        source = f"{len(comp_roi)} tone-comparable titles"

    return FilmProjection(
        budget_usd=budget_usd,
        break_even_gross_usd=break_even_gross,
        bear_gross_usd=int(budget_usd * bear),
        base_gross_usd=int(budget_usd * base),
        bull_gross_usd=int(budget_usd * bull),
        bear_roi=round(bear, 2),
        base_roi=round(base, 2),
        bull_roi=round(bull, 2),
        probability_break_even_pct=round(break_even_pct, 1),
        probability_hit_pct=round(hit_pct, 1),
        comp_sample_size=sample,
        assumptions=[
            Assumption("break_even_multiple", THEATRICAL_BREAK_EVEN_MULTIPLE,
                       "Worldwide gross needed per dollar of negative cost: distributor keeps "
                       f"{DISTRIBUTOR_SHARE:.0%} of box office, P&A adds {PA_SPEND_RATIO:.0%} of budget, "
                       "ancillary revenue offsets 0.5x."),
            Assumption("evidence_source", source,
                       "Break-even probability is drawn from a budget-matched sample when one "
                       "exists, and from the tone-comparable set otherwise."),
            Assumption("scenario_quantiles", [0.20, 0.50, 0.85],
                       "Bear / base / bull are the 20th, 50th and 85th percentile of realised "
                       "ROI, blending the budget band with the tone comparables."),
            Assumption("selection_bias", "budgets are not randomly assigned",
                       "A budget band shows what happened to films a studio was already "
                       "willing to spend that much on. Films only reach a $150m budget after "
                       "someone believed in them, so a high band hit-rate partly measures that "
                       "belief, not the money. Read the band as the company this project would "
                       "keep, not as a promise about this project."),
            Assumption("inflation", "nominal",
                       "Historical grosses are nominal USD, not adjusted for inflation."),
        ],
    )


def project_series(
    per_episode_budget_usd: int,
    episodes: int,
    comps: list[dict],
    benchmark: Optional[dict] = None,
) -> SeriesProjection:
    """Television is not judged on ROI - it is judged on whether it comes back."""
    seasons = [int(c["number_of_seasons"]) for c in comps if c.get("number_of_seasons")]
    renewed = [1 for c in comps if c.get("returned_after_s1")]
    cancelled = [1 for c in comps if c.get("did_not_return")]
    n = len(comps)

    if n >= 4:
        renewal_pct = round(100 * len(renewed) / n, 1)
        cancel_pct = round(100 * len(cancelled) / n, 1)
        reach_s3 = round(100 * sum(1 for s in seasons if s >= 3) / n, 1) if seasons else 0.0
        expected = round(statistics.fmean(seasons), 2) if seasons else 1.0
        sample = n
    elif benchmark:
        renewal_pct = float(benchmark.get("pct_returned_after_s1") or 0.0)
        cancel_pct = float(benchmark.get("pct_did_not_return") or 0.0)
        reach_s3 = float(benchmark.get("pct_reached_s3") or 0.0)
        expected = float(benchmark.get("avg_seasons") or 1.0)
        sample = int(benchmark.get("sample_size") or 0)
    else:
        raise ValueError("No series evidence available: cannot project without comparables.")

    return SeriesProjection(
        per_episode_budget_usd=per_episode_budget_usd,
        episodes=episodes,
        season_cost_usd=per_episode_budget_usd * episodes,
        renewal_probability_pct=renewal_pct,
        cancellation_risk_pct=cancel_pct,
        expected_seasons=expected,
        reach_season_three_pct=reach_s3,
        comp_sample_size=sample,
        assumptions=[
            Assumption("renewal_definition", "number_of_seasons >= 2",
                       "A show counts as having returned when Wikidata records a second season."),
            Assumption("did_not_return_definition", "one season, and an end date is recorded",
                       "Wikidata has no cancellation flag, so a single-season show that has "
                       "ended stands in for one that was not renewed. A single-season show "
                       "still in production is not counted against it."),
            Assumption("no_viewership_data", True,
                       "Platforms do not publish viewing figures, so renewal outcomes stand in "
                       "for audience performance."),
        ],
    )


# --------------------------------------------------------------------------
# Greenlight score
# --------------------------------------------------------------------------

@dataclass
class Score:
    value: int
    verdict: str
    components: dict[str, float]

    def to_dict(self) -> dict:
        return {"value": self.value, "verdict": self.verdict, "components": self.components}


def _band(value: float, lo: float, hi: float) -> float:
    """Map value into 0..1 across [lo, hi], clamped."""
    if hi == lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def score_film(projection: FilmProjection, comps: list[dict], budget_usd: int,
               benchmark: Optional[dict]) -> Score:
    evidence = _band(projection.comp_sample_size, 2, 8)
    upside = _band(projection.base_roi, 1.0, 4.0)
    downside = _band(projection.probability_break_even_pct, 20.0, 80.0)
    scored = [float(c["audience_score"]) for c in comps if c.get("has_audience_score")]
    # No score at all is not the same as a bad score: fall back to neutral so a
    # thinly documented comp set cannot silently drag the verdict down.
    reception = _band(statistics.fmean(scored), 40.0, 85.0) if scored else 0.5
    median_budget = float((benchmark or {}).get("median_budget_usd") or budget_usd or 1)
    # Being far above the genre's median budget is the single most common way a
    # film fails to recoup, so overshooting is penalised, undershooting is not.
    overshoot = budget_usd / median_budget if median_budget else 1.0
    discipline = 1.0 - _band(overshoot, 1.0, 3.0)

    components = {
        "downside_protection": round(downside, 3),
        "upside": round(upside, 3),
        "comp_reception": round(reception, 3),
        "budget_discipline": round(discipline, 3),
        "evidence_strength": round(evidence, 3),
    }
    weights = {
        "downside_protection": 0.34,
        "upside": 0.24,
        "comp_reception": 0.14,
        "budget_discipline": 0.18,
        "evidence_strength": 0.10,
    }
    raw = sum(components[k] * w for k, w in weights.items())
    value = int(round(raw * 100))
    return Score(value=value, verdict=_verdict(value), components=components)


def score_series(projection: SeriesProjection, comps: list[dict],
                 benchmark: Optional[dict]) -> Score:
    evidence = _band(projection.comp_sample_size, 2, 12)
    renewal = _band(projection.renewal_probability_pct, 20.0, 80.0)
    longevity = _band(projection.expected_seasons, 1.0, 4.0)
    survival = 1.0 - _band(projection.cancellation_risk_pct, 10.0, 60.0)
    # Wikidata carries review scores for films but almost never for series, so
    # television is scored on outcomes alone rather than on a field that would
    # be empty for every row.
    components = {
        "renewal_likelihood": round(renewal, 3),
        "cancellation_safety": round(survival, 3),
        "longevity": round(longevity, 3),
        "evidence_strength": round(evidence, 3),
    }
    weights = {
        "renewal_likelihood": 0.38,
        "cancellation_safety": 0.28,
        "longevity": 0.22,
        "evidence_strength": 0.12,
    }
    raw = sum(components[k] * w for k, w in weights.items())
    value = int(round(raw * 100))
    return Score(value=value, verdict=_verdict(value), components=components)


def _verdict(value: int) -> str:
    if value >= 72:
        return "GREENLIT"
    if value >= 58:
        return "CONDITIONAL"
    if value >= 42:
        return "RESHAPE"
    return "PASS"
