"""Runaway protection for the agent loop.

An LLM agent with tools can loop: it calls a tool, dislikes the answer, calls it
again with almost the same arguments, forever. Left alone that burns money and
hangs the request. This module puts six independent limits in front of every
model call and every tool call, using ADK's before_model / before_tool hooks.

Design choice: when a limit trips we do NOT raise. We hand the model a plain
message telling it to stop and answer with what it already has. A truncated
answer built on real evidence is worth more than a stack trace, and the trip is
recorded in the request trace so it is visible rather than silent.

The only hard failure is the wall-clock deadline, enforced outside by
asyncio.wait_for, because a hung request has to end somehow.
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from app.agents.context import RequestContext, ToolCall
from app.config import settings
from app.cost import BudgetExceeded

logger = logging.getLogger("greenlight.guardrails")


@dataclass
class Limits:
    """Every number here is a ceiling, not a target."""

    max_model_calls: int = 14          # whole request, across all three agents
    max_tool_calls: int = 24           # whole request
    max_calls_per_tool: int = 10       # one tool used over and over
    max_identical_calls: int = 2       # same tool, same arguments - a loop
    deadline_seconds: int = field(default_factory=lambda: settings().agent_timeout_sec)
    max_cost_usd: float = field(default_factory=lambda: settings().max_cost_usd_per_request)

    @classmethod
    def strict(cls) -> "Limits":
        """Tighter set for the What-if path, which must stay interactive."""
        return cls(max_model_calls=4, max_tool_calls=6, max_calls_per_tool=3,
                   max_identical_calls=1, deadline_seconds=30)


class RunawayGuard:
    """One instance per request. Not thread-safe by design: one request, one loop."""

    def __init__(self, ctx: RequestContext, limits: Optional[Limits] = None) -> None:
        self.ctx = ctx
        self.limits = limits or Limits()
        self.started = time.monotonic()
        self.model_calls = 0
        self.tool_calls = 0
        self.per_tool: Counter[str] = Counter()
        self.signatures: Counter[str] = Counter()
        self.trips: list[str] = []

    # -- helpers ---------------------------------------------------------

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def _trip(self, reason: str) -> str:
        if reason not in self.trips:
            self.trips.append(reason)
            logger.warning("GUARDRAIL request=%s tripped: %s", self.ctx.request_id, reason)
            self.ctx.record(ToolCall(
                agent=self.ctx.current_agent, tool="guardrail", arguments={},
                elapsed_ms=0.0, backend="none", row_count=0, error=reason,
            ))
        return reason

    @staticmethod
    def _signature(tool_name: str, args: dict[str, Any]) -> str:
        try:
            payload = json.dumps(args, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001
            payload = repr(sorted(args))
        return f"{tool_name}::{payload}"

    def status(self) -> dict:
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "elapsed_seconds": round(self.elapsed, 2),
            "limits": {
                "max_model_calls": self.limits.max_model_calls,
                "max_tool_calls": self.limits.max_tool_calls,
                "max_calls_per_tool": self.limits.max_calls_per_tool,
                "max_identical_calls": self.limits.max_identical_calls,
                "deadline_seconds": self.limits.deadline_seconds,
                "max_cost_usd": self.limits.max_cost_usd,
            },
            "tripped": self.trips,
        }

    # -- ADK hooks -------------------------------------------------------

    def before_model(self, context, llm_request):
        """Runs before every LLM call. Returning a response cancels the call."""
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types as genai_types

        reason = None
        if self.elapsed > self.limits.deadline_seconds:
            reason = (f"Time limit reached ({self.limits.deadline_seconds}s).")
        elif self.model_calls >= self.limits.max_model_calls:
            reason = (f"Model call limit reached ({self.limits.max_model_calls}).")
        elif self.ctx.meter.usd >= self.limits.max_cost_usd:
            reason = (f"Cost ceiling reached (${self.limits.max_cost_usd:.2f}).")

        if reason:
            self._trip(reason)
            text = (
                f"[stopped by guardrail] {reason} Answer now using only the evidence "
                f"already gathered, and state plainly that the investigation was cut short."
            )
            return LlmResponse(
                content=genai_types.Content(role="model", parts=[genai_types.Part(text=text)])
            )

        self.model_calls += 1
        return None

    def before_tool(self, tool, args: dict[str, Any], tool_context):
        """Runs before every tool call. Returning a dict replaces the tool result."""
        name = getattr(tool, "name", str(tool))
        signature = self._signature(name, args)

        reason = None
        if self.elapsed > self.limits.deadline_seconds:
            reason = f"Time limit reached ({self.limits.deadline_seconds}s)."
        elif self.tool_calls >= self.limits.max_tool_calls:
            reason = f"Tool call limit reached ({self.limits.max_tool_calls})."
        elif self.per_tool[name] >= self.limits.max_calls_per_tool:
            reason = f"'{name}' has already been called {self.per_tool[name]} times."
        elif self.signatures[signature] >= self.limits.max_identical_calls:
            reason = (
                f"'{name}' has already been called with these exact arguments "
                f"{self.signatures[signature]} times. Repeating it will return the same rows."
            )
        elif self.ctx.meter.usd >= self.limits.max_cost_usd:
            reason = f"Cost ceiling reached (${self.limits.max_cost_usd:.2f})."

        if reason:
            self._trip(reason)
            return {
                "error": f"[stopped by guardrail] {reason}",
                "instruction": "Do not call this tool again. Summarise what you already have.",
                "elapsed_ms": 0.0,
                "backend": "none",
                "row_count": 0,
            }

        self.tool_calls += 1
        self.per_tool[name] += 1
        self.signatures[signature] += 1
        return None

    def attach(self, agent) -> None:
        """Wire this guard into an ADK agent."""
        agent.before_model_callback = self.before_model
        agent.before_tool_callback = self.before_tool


class DeadlineExceeded(RuntimeError):
    """The wall clock ran out even though the soft limits did not stop it."""


async def with_deadline(coro, seconds: int, request_id: str):
    """Outer backstop. The soft limits should always fire first."""
    import asyncio

    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError as exc:
        logger.error("GUARDRAIL request=%s hard deadline %ss exceeded", request_id, seconds)
        raise DeadlineExceeded(
            f"Request {request_id} exceeded its {seconds}s hard deadline and was cancelled."
        ) from exc
