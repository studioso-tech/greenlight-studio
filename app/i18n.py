"""UI strings in English and Japanese.

English is the default and the language of the submission. Japanese exists
because the catalogue carries 346 Japanese series and 1,015 Korean ones, and a
producer working in Tokyo should be able to read the verdict in their own
language without the numbers changing underneath them.

Only the wording is translated. Every figure comes from the same computation.
"""
from __future__ import annotations

from typing import Any

LOCALES = ("en", "ja")
DEFAULT = "en"

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "app.title": "Greenlight Studio",
        "app.tagline": "Decide with the record, not the room",
        "mode.film": "Film",
        "mode.series": "Series",
        "input.material": "Screenplay, treatment or series bible",
        "input.material.hint": "Paste the material. A logline is enough to start.",
        "input.budget": "Production budget",
        "input.per_episode": "Budget per episode",
        "input.episodes": "Episodes",
        "input.release_month": "Target release month",
        "action.analyze": "Run the committee",
        "action.analyzing": "Working…",
        "action.whatif": "What if",
        "verdict.GREENLIT": "Greenlit",
        "verdict.CONDITIONAL": "Conditional",
        "verdict.RESHAPE": "Reshape",
        "verdict.PASS": "Pass",
        "panel.score": "Greenlight score",
        "panel.breakdown": "What the score is made of",
        "comp.downside_protection": "Downside protection",
        "comp.upside": "Upside",
        "comp.comp_reception": "How comparables were received",
        "comp.budget_discipline": "Budget discipline",
        "comp.evidence_strength": "Strength of evidence",
        "comp.renewal_vs_market": "Renewal, against this market",
        "comp.continuation_safety": "Safety from an early ending",
        "comp.longevity": "Expected run, against this market",
        "comp.order_fit": "Episode order vs. this market",
        "comps.excluded_note": "Excluding evidence changes the verdict. That is the point, and the reason it is shown.",
        "panel.projection": "Projection",
        "panel.comps": "Comparable titles",
        "panel.memo": "The memo",
        "panel.trace": "What the agents did",
        "panel.assumptions": "Assumptions",
        "panel.whatif": "Move a lever",
        "panel.tipping": "Where the verdict changes",
        "tipping.none": "The verdict does not change anywhere along this lever. Something other than the budget has to move.",
        "tipping.at": "At {value} this becomes {verdict}.",
        "tipping.current": "now",
        "tipping.loading": "Working out where it turns…",
        "field.break_even": "Break-even gross",
        "field.bear": "Bear",
        "field.base": "Base",
        "field.bull": "Bull",
        "field.probability": "Reaches break-even",
        "field.renewal": "Returns for season 2",
        "field.cancellation": "Ends after one season",
        "field.expected_seasons": "Expected seasons",
        "field.season_cost": "Season cost",
        "field.sample": "Sample",
        "field.budget_band": "Budget band",
        "meta.clickhouse": "ClickHouse",
        "meta.cost": "Model cost",
        "meta.elapsed": "Elapsed",
        "meta.generated_sql": "SQL the analyst wrote",
        "note.nominal": "Figures are nominal USD, not inflation adjusted.",
        "note.sources": "Catalogue built from Wikidata (CC0) and English Wikipedia (CC BY-SA 4.0).",
        "error.generic": "Something went wrong. The details are below.",
        "error.budget": "This request hit its spending ceiling and was stopped.",
    },
    "ja": {
        "app.title": "Greenlight Studio",
        "app.tagline": "会議室の空気ではなく、実績で決める",
        "mode.film": "映画",
        "mode.series": "ドラマ",
        "input.material": "脚本・トリートメント・シリーズ企画書",
        "input.material.hint": "本文を貼り付けてください。ログライン一行でも始められます。",
        "input.budget": "製作費",
        "input.per_episode": "1話あたり製作費",
        "input.episodes": "話数",
        "input.release_month": "公開予定月",
        "action.analyze": "審議にかける",
        "action.analyzing": "調査中…",
        "action.whatif": "条件を変える",
        "verdict.GREENLIT": "承認",
        "verdict.CONDITIONAL": "条件付き",
        "verdict.RESHAPE": "再設計",
        "verdict.PASS": "見送り",
        "panel.score": "承認スコア",
        "panel.breakdown": "スコアの内訳",
        "comp.downside_protection": "下方耐性",
        "comp.upside": "上振れ余地",
        "comp.comp_reception": "類似作の評価",
        "comp.budget_discipline": "予算規律",
        "comp.evidence_strength": "証拠の厚み",
        "comp.renewal_vs_market": "更新確率（市場比）",
        "comp.continuation_safety": "早期終了の回避",
        "comp.longevity": "継続年数（市場比）",
        "comp.order_fit": "話数の市場適合",
        "comps.excluded_note": "証拠を外せば結論は変わります。それが分かるように表示しています。",
        "panel.projection": "興行予測",
        "panel.comps": "類似作",
        "panel.memo": "審議メモ",
        "panel.trace": "エージェントの実行記録",
        "panel.assumptions": "前提",
        "panel.whatif": "条件を動かす",
        "panel.tipping": "判定が変わる地点",
        "tipping.none": "この範囲では判定が変わりません。予算以外を動かす必要があります。",
        "tipping.at": "{value} で {verdict} に変わります。",
        "tipping.current": "現在",
        "tipping.loading": "変化点を計算中…",
        "field.break_even": "損益分岐に必要な興収",
        "field.bear": "悲観",
        "field.base": "標準",
        "field.bull": "楽観",
        "field.probability": "損益分岐到達確率",
        "field.renewal": "シーズン2に進む確率",
        "field.cancellation": "1シーズンで終了する確率",
        "field.expected_seasons": "期待シーズン数",
        "field.season_cost": "1シーズンの総製作費",
        "field.sample": "標本数",
        "field.budget_band": "予算帯",
        "meta.clickhouse": "ClickHouse",
        "meta.cost": "モデル費用",
        "meta.elapsed": "所要時間",
        "meta.generated_sql": "エージェントが書いたSQL",
        "note.nominal": "金額は名目USDで、インフレ調整をしていません。",
        "note.sources": "データはWikidata（CC0）と英語版Wikipedia（CC BY-SA 4.0）から構築しています。",
        "error.generic": "処理に失敗しました。詳細は下記のとおりです。",
        "error.budget": "このリクエストは費用上限に達したため停止しました。",
    },
}


def normalise(locale: str | None) -> str:
    if not locale:
        return DEFAULT
    short = locale.split("-")[0].lower()
    return short if short in LOCALES else DEFAULT


def bundle(locale: str | None = None) -> dict[str, Any]:
    resolved = normalise(locale)
    # Fall back key by key, so a missing Japanese string shows English rather
    # than an empty box.
    merged = dict(STRINGS[DEFAULT])
    merged.update(STRINGS[resolved])
    return {"locale": resolved, "available": list(LOCALES), "strings": merged}
