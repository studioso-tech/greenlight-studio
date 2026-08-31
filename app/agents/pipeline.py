"""The agent pipeline.

Three ADK agents, with deterministic Python between them:

  1. Script Analyst   - reads the material, returns a structured brief (no tools)
  2. Research Analyst - investigates the catalogue with real ClickHouse tools,
                        including SQL it writes itself
  3. Greenlight Writer- turns the evidence into a memo a human can argue with

Between 2 and 3 the projection and the score are computed in Python from the
rows the tools returned. The model never does the arithmetic, so the memo and
the chart can never disagree, and the What-if sliders re-run without a model
call.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal, Optional

from google.genai import types as genai_types
from pydantic import BaseModel, Field

from app.agents import clickhouse_mcp
from app.agents import tools as agent_tools
from app.agents.context import RequestContext
from app.config import settings
from app.llm import adk_model
from app.store.queries import SCHEMA_CARD

logger = logging.getLogger("greenlight.pipeline")

APP_NAME = "greenlight-studio"


def _config(max_output_tokens: int = 2048):
    """Generation config shared by every agent.

    Thinking is off. Measured on this pipeline: the same extraction takes 0.8s
    with thinking disabled and 5-29s with the 2.5-flash default, because the
    model spends 600-800 thought tokens on it. None of these three agents is
    doing open-ended reasoning - one extracts fields, one picks queries from a
    documented schema, one writes prose from numbers it is forbidden to alter -
    so the thinking budget buys nothing and costs the demo its responsiveness.
    """
    from google.genai import types as genai_types

    return genai_types.GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=max_output_tokens,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
    )


# --------------------------------------------------------------------------
# Structured brief
# --------------------------------------------------------------------------

class ScriptBrief(BaseModel):
    normalised_title: str = Field(description="The project title, cleaned up.")
    primary_genre: str = Field(
        description="One TMDB genre name: Action, Adventure, Animation, Comedy, Crime, "
        "Documentary, Drama, Family, Fantasy, History, Horror, Music, Mystery, Romance, "
        "Science Fiction, Thriller, War, Western."
    )
    secondary_genres: list[str] = Field(default_factory=list)
    logline: str = Field(description="One sentence: protagonist, want, obstacle, stakes.")
    tone_and_subject: str = Field(
        description="Three or four sentences describing tone, register, visual world and "
        "thematic subject. This text is embedded for similarity search, so it should read "
        "like a synopsis, not like notes."
    )
    themes: list[str] = Field(default_factory=list)
    setting: str = ""
    protagonist: str = ""
    audience: str = Field(default="", description="Who this is for, in one line.")
    suggested_rating: str = Field(default="", description="G, PG, PG-13, R, or a TV equivalent.")
    vfx_load: Literal["low", "medium", "high", "extreme"] = "medium"
    original_language: str = Field(
        default="English",
        description="The language the project is made in, written as its English NAME "
        "- 'English', 'Japanese', 'Korean', 'Spanish'. Not an ISO code: the catalogue "
        "stores names, and a code matches nothing.",
    )
    risks_noticed: list[str] = Field(
        default_factory=list,
        description="Production or market risks visible in the material itself.",
    )


def embedding_text(brief: ScriptBrief) -> str:
    parts = [brief.logline, brief.tone_and_subject]
    if brief.themes:
        parts.append("Themes: " + ", ".join(brief.themes))
    if brief.setting:
        parts.append("Setting: " + brief.setting)
    return "\n".join(p for p in parts if p)


# --------------------------------------------------------------------------
# Agents
# --------------------------------------------------------------------------

def _script_analyst():
    from google.adk.agents import LlmAgent

    return LlmAgent(
        name="script_analyst",
        model=adk_model(settings().reasoning_model),
        description="Reads a screenplay, treatment or series bible and extracts a structured brief.",
        instruction=(
            "You are a development executive's reader. You are given raw material for a project: "
            "a screenplay extract, a treatment, a logline, or a series bible.\n\n"
            "Extract what is actually on the page. Do not invent plot, cast or budget. "
            "If the material is thin, say so through a shorter tone_and_subject rather than "
            "padding it with invention.\n\n"
            "Write every field in English, whatever language the source material is in. "
            "normalised_title: give the established English release title if the work has one, "
            "otherwise a transliteration or a plain English rendering - never leave it in the "
            "original script. logline and tone_and_subject must be English prose: they are "
            "embedded and matched against an English-language catalogue, so a non-English "
            "brief quietly weakens every comparable that follows.\n\n"
            "tone_and_subject is the most important field: it becomes an embedding that is "
            "matched against thousands of real titles, so write it as flowing synopsis prose "
            "that captures register and subject, not as a list of adjectives."
        ),
        output_schema=ScriptBrief,
        output_key="brief",
        generate_content_config=_config(1536),
    )


_RESEARCH_INSTRUCTION = """
You are a research analyst inside a film and television studio's greenlight
committee. You have live read-only access to a ClickHouse catalogue of real
titles and their real commercial outcomes.

{schema_card}

You are reviewing this project:

{brief_json}

The reviewer proposes: {proposal}

Your job is to assemble the evidence a greenlight decision needs. Work like an
analyst, not like a search box:

1. Call describe_catalogue first. Never claim coverage the data does not have.
2. Call find_comparable_titles to get tone-similar titles. If the first result
   set is thin or the genre filter was too narrow, widen it and call again.
3. Call benchmark_segment for the segment this project sits in.
4. Write your own SQL and run it with clickhouse_run_query. This is where you
   earn your keep, and it is not optional: every review must include at least
   one query you composed yourself. Pick a question that would change the
   decision, for example:
     - does this budget band behave differently from the genre as a whole
     - how does the proposed release month compare to other months
     - do titles in this original language behave differently
     - for series, how does this network's or this episode count's record look
   Write the SQL yourself. Read the schema above; it is the whole database.
   If the query is rejected, read the error and fix it. Always add a LIMIT.
5. For film, call rank_talent for the primary genre if casting or directing
   attachments would move the number.

Stop when more queries would not change the recommendation. Then reply with
your findings in plain prose: what the comparable set says, what the segment
says, what your own query found, and where the evidence is weak or missing.
Quote concrete figures and title names. Do not give a verdict or a score -
that is computed elsewhere. Do not write the memo.
""".strip()


def _research_analyst(brief: ScriptBrief, proposal: str):
    from google.adk.agents import LlmAgent

    return LlmAgent(
        name="research_analyst",
        model=adk_model(settings().reasoning_model),
        description="Investigates the ClickHouse catalogue, writing its own SQL when needed.",
        instruction=_RESEARCH_INSTRUCTION.format(
            schema_card=SCHEMA_CARD,
            brief_json=brief.model_dump_json(indent=2),
            proposal=proposal,
        ),
        tools=_research_tools(),
        generate_content_config=_config(2048),
    )


def _research_tools() -> list:
    """Our measured tools for what the partner's server cannot do, plus the
    official ClickHouse MCP server for everything the model writes itself.

    The ad-hoc SQL path is the official server's, exclusively. Offering both it
    and our own query_catalogue left the choice to the model, and on the first
    production run the model picked ours - which means a requirement of the
    track would have been satisfied only when the model happened to feel like
    it. Removing the alternative makes it structural.

    Our tools keep what a generic SQL server has no notion of: the embedding
    vector search, the budget-band benchmark, and the measured per-call latency
    the interface displays.

    query_catalogue survives only as a fallback for when the MCP server fails
    to start, so a demo never dies on a subprocess.
    """
    toolset = clickhouse_mcp.shared_toolset()
    if toolset is None:
        logger.warning("mcp-clickhouse unavailable; falling back to the direct SQL tool")
        return list(agent_tools.RESEARCH_TOOLS)
    tools = [t for t in agent_tools.RESEARCH_TOOLS if t is not agent_tools.query_catalogue]
    tools.append(toolset)
    return tools


_WRITER_INSTRUCTION = """
You are the studio head writing the greenlight memo that the committee will
argue over. Write it in {language_name}.

The project:
{brief_json}

The proposal on the table: {proposal}

Your analyst's findings:
{findings}

The numbers, already computed from the database rows (do not recompute them,
do not contradict them, quote them exactly as given):
{numbers_json}

The evidence rows the analyst pulled:
{evidence_json}

Write the memo with these sections, using the section names in {language_name}:

VERDICT - one paragraph. The committee's decision is: {verdict_directive}
(internal code: {verdict}). State that decision plainly, in your own words, in
{language_name} - do not just translate the English code above. Then give the
score ({score}/100) and say plainly what the decision rests on. If the
decision is to decline the project, do not use approval language ("approve",
"greenlit", "承認") anywhere in this paragraph.

WHY - three to five bullets. Each bullet must cite a specific comparable title
with its real figures, or a specific statistic from the analyst's queries. A
bullet with no number in it is not a bullet, delete it.

WHAT WOULD CHANGE THIS - two or three bullets naming the concrete change that
would move the verdict, with the direction and rough size of the move.

WHERE THIS COULD BE WRONG - be honest. Name the limits of the evidence: sample
size, that grosses are nominal not inflation adjusted, missing data, anything
the analyst flagged as weak.

Rules:
- Every figure you state must appear in the numbers or evidence above. If you
  want to say something you cannot source, say that it is not in the data.
- No adjectives doing the work of evidence. "Strong potential" means nothing.
- Do not use markdown headers or bold. Plain section names and plain bullets.
""".strip()

_LANGUAGE_NAME = {"en": "English", "ja": "Japanese"}

# The scorer's internal codes (scoring.py:_verdict) are Hollywood shorthand,
# not plain English - "PASS" means "we are passing on this / declining it",
# not "it passed". Handed to the writer model bare, "PASS" reads as approval
# about as often as it reads as decline, and the memo's own VERDICT paragraph
# ends up arguing the opposite of what the summary table says. Spelling the
# decision out removes the ambiguity instead of relying on the model to know
# the jargon.
_VERDICT_DIRECTIVE = {
    "GREENLIT": "approve this project as proposed",
    "CONDITIONAL": "approve this project only if the conditions below are met",
    "RESHAPE": "do not approve this project as submitted - it needs to be restructured first",
    "PASS": "decline this project - do not greenlight it",
}


def _greenlight_writer(brief, proposal, findings, numbers, evidence, locale: str):
    from google.adk.agents import LlmAgent

    verdict_code = numbers.get("score", {}).get("verdict", "")
    return LlmAgent(
        name="greenlight_writer",
        model=adk_model(settings().writer_model),
        description="Writes the greenlight memo from evidence and computed numbers.",
        instruction=_WRITER_INSTRUCTION.format(
            language_name=_LANGUAGE_NAME.get(locale, "English"),
            brief_json=brief.model_dump_json(indent=2),
            proposal=proposal,
            findings=findings,
            numbers_json=json.dumps(numbers, ensure_ascii=False, indent=2, default=str),
            evidence_json=json.dumps(evidence, ensure_ascii=False, indent=2, default=str)[:12000],
            verdict=verdict_code,
            verdict_directive=_VERDICT_DIRECTIVE.get(verdict_code, verdict_code),
            score=numbers.get("score", {}).get("value", ""),
        ),
        generate_content_config=_config(2048),
    )


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

async def run_agent(agent, prompt: str, ctx: RequestContext, guard=None) -> str:
    """Run one ADK agent to completion and return its final text.

    The guard is shared across all three agents in a request, so its counters
    bound the whole pipeline rather than each stage separately.
    """
    from google.adk.runners import InMemoryRunner

    ctx.current_agent = agent.name
    if guard is not None:
        guard.attach(agent)
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=ctx.request_id
    )
    message = genai_types.Content(role="user", parts=[genai_types.Part(text=prompt)])

    final_text = ""
    # agent.model is now a Gemini object, not a string; the meter needs the name.
    model_field = getattr(agent, "model", None)
    model = getattr(model_field, "model", None) or str(model_field or settings().reasoning_model)
    try:
        async for event in runner.run_async(
            user_id=ctx.request_id, session_id=session.id, new_message=message
        ):
            usage = getattr(event, "usage_metadata", None)
            if usage is not None:
                ctx.meter.record(
                    str(model),
                    int(getattr(usage, "prompt_token_count", 0) or 0),
                    int(getattr(usage, "candidates_token_count", 0) or 0),
                )
            if event.content and event.content.parts:
                text = "".join(p.text or "" for p in event.content.parts)
                if text.strip():
                    final_text = text
    finally:
        close = getattr(runner, "close", None)
        if close is not None:
            result = close()
            if asyncio.iscoroutine(result):
                await result

    return final_text.strip()


async def extract_brief(material: str, ctx: RequestContext, guard=None) -> ScriptBrief:
    raw = await run_agent(_script_analyst(), material, ctx, guard)
    try:
        return ScriptBrief.model_validate_json(raw)
    except Exception:  # noqa: BLE001
        # The model occasionally wraps JSON in a fence despite output_schema.
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        return ScriptBrief.model_validate_json(cleaned.strip())


async def research(brief: ScriptBrief, proposal: str, ctx: RequestContext, guard=None) -> str:
    agent = _research_analyst(brief, proposal)
    return await run_agent(
        agent,
        "Assemble the evidence for this greenlight decision. Begin.",
        ctx,
        guard,
    )


async def write_memo(brief, proposal, findings, numbers, evidence, ctx: RequestContext, guard=None) -> str:
    agent = _greenlight_writer(brief, proposal, findings, numbers, evidence, ctx.locale)
    return await run_agent(agent, "Write the greenlight memo now.", ctx, guard)
