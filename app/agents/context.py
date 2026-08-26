"""Per-request state that agent tools read without the model having to carry it.

A 768-float embedding must never travel through the model's context window, and
the cost meter must be shared by every tool call in one request. Both live here,
scoped to the request via contextvars so concurrent requests stay isolated.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from app.cost import CostMeter

Mode = Literal["film", "series"]


@dataclass
class ToolCall:
    """One measured call, surfaced to the UI as the agent execution trace."""

    agent: str
    tool: str
    arguments: dict[str, Any]
    elapsed_ms: float
    backend: str
    row_count: int
    sql: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RequestContext:
    request_id: str
    mode: Mode
    meter: CostMeter
    embedding: list[float] = field(default_factory=list)
    locale: str = "en"
    trace: list[ToolCall] = field(default_factory=list)
    current_agent: str = "orchestrator"

    # Evidence captured straight from the tool layer rather than parsed back out
    # of the model's prose, so the arithmetic can never drift from what the
    # database returned.
    comps: list[dict] = field(default_factory=list)
    benchmark: dict = field(default_factory=dict)
    talent: list[dict] = field(default_factory=list)

    def record(self, call: ToolCall) -> None:
        self.trace.append(call)

    @property
    def clickhouse_ms(self) -> float:
        return round(sum(c.elapsed_ms for c in self.trace if c.backend != "none"), 2)


_ctx: contextvars.ContextVar[Optional[RequestContext]] = contextvars.ContextVar(
    "greenlight_request_context", default=None
)


def set_context(ctx: RequestContext) -> contextvars.Token:
    return _ctx.set(ctx)


def reset_context(token: contextvars.Token) -> None:
    _ctx.reset(token)


def current() -> RequestContext:
    ctx = _ctx.get()
    if ctx is None:
        raise RuntimeError("No RequestContext is active; tools must run inside a request.")
    return ctx
