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

from app.agents import tools as agent_tools
from app.agents.context import RequestContext
from app.config import settings
from app.store.queries import SCHEMA_CARD

logger = logging.getLogger("greenlight.pipeline")

APP_NAME = "greenlight-studio"


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
        default="en", description="ISO-639-1 code of the language the project is made in."
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
        model=settings().reasoning_model,
        description="Reads a screenplay, treatment or series bible and extracts a structured brief.",
        instruction=(
            "You are a development executive's reader. You are given raw material for a project: "
            "a screenplay extract, a treatment, a logline, or a series bible.\n\n"
            "Extract what is actually on the page. Do not invent plot, cast or budget. "
            "If the material is thin, say so through a shorter tone_and_subject rather than "
            "padding it with invention.\n\n"
            "tone_and_subject is the most important field: it becomes an embedding that is "
            "matched against thousands of real titles, so write it as flowing synopsis prose "
            "that captures register and subject, not as a list of adjectives."
        ),
        output_schema=ScriptBrief,
        output_key="brief",
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
4. Use query_catalogue to answer the question the standard tools cannot. This is
   where you earn your keep. Pick questions that would change the decision, for
   example:
     - does this budget band behave differently from the genre as a whole
     - how does the proposed release month compare to other months
     - do titles in this original language behave differently
     - for series, how does this network's or this episode count's record look
   Write the SQL yourself. If it is rejected, read the error and fix it.
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
        model=settings().reasoning_model,
        description="Investigates the ClickHouse catalogue, writing its own SQL when needed.",
        instruction=_RESEARCH_INSTRUCTION.format(
            schema_card=SCHEMA_CARD,
            brief_json=brief.model_dump_json(indent=2),
            proposal=proposal,
        ),
        tools=list(agent_tools.RESEARCH_TOOLS),
    )


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

VERDICT - one paragraph. State the verdict ({verdict}) and the score
({score}/100) and say plainly what it rests on.

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


def _greenlight_writer(brief, proposal, findings, numbers, evidence, locale: str):
    from google.adk.agents import LlmAgent

    return LlmAgent(
        name="greenlight_writer",
        model=settings().writer_model,
        description="Writes the greenlight memo from evidence and computed numbers.",
        instruction=_WRITER_INSTRUCTION.format(
            language_name=_LANGUAGE_NAME.get(locale, "English"),
            brief_json=brief.model_dump_json(indent=2),
            proposal=proposal,
            findings=findings,
            numbers_json=json.dumps(numbers, ensure_ascii=False, indent=2, default=str),
            evidence_json=json.dumps(evidence, ensure_ascii=False, indent=2, default=str)[:12000],
            verdict=numbers.get("score", {}).get("verdict", ""),
            score=numbers.get("score", {}).get("value", ""),
        ),
    )


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

async def run_agent(agent, prompt: str, ctx: RequestContext) -> str:
    """Run one ADK agent to completion and return its final text."""
    from google.adk.runners import InMemoryRunner

    ctx.current_agent = agent.name
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=ctx.request_id
    )
    message = genai_types.Content(role="user", parts=[genai_types.Part(text=prompt)])

    final_text = ""
    model = getattr(agent, "model", settings().reasoning_model)
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


async def extract_brief(material: str, ctx: RequestContext) -> ScriptBrief:
    raw = await run_agent(_script_analyst(), material, ctx)
    try:
        return ScriptBrief.model_validate_json(raw)
    except Exception:  # noqa: BLE001
        # The model occasionally wraps JSON in a fence despite output_schema.
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        return ScriptBrief.model_validate_json(cleaned.strip())


async def research(brief: ScriptBrief, proposal: str, ctx: RequestContext) -> str:
    agent = _research_analyst(brief, proposal)
    return await run_agent(
        agent,
        "Assemble the evidence for this greenlight decision. Begin.",
        ctx,
    )


async def write_memo(brief, proposal, findings, numbers, evidence, ctx: RequestContext) -> str:
    agent = _greenlight_writer(brief, proposal, findings, numbers, evidence, ctx.locale)
    return await run_agent(agent, "Write the greenlight memo now.", ctx)
