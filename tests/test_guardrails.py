"""Every limit in RunawayGuard, exercised by actually tripping it.

These run without a model or a database: the guard is pure bookkeeping, which
is the point - the protection cannot depend on the thing it is protecting.
"""
from __future__ import annotations

import sys
import time
from types import SimpleNamespace

from app.agents.context import RequestContext
from app.agents.guardrails import Limits, RunawayGuard
from app.cost import CostMeter


def make_guard(**overrides) -> RunawayGuard:
    ctx = RequestContext(
        request_id="guard-test", mode="film", meter=CostMeter("guard-test")
    )
    return RunawayGuard(ctx, Limits(**overrides))


def tool(name: str):
    return SimpleNamespace(name=name)


results: list[tuple[str, bool, str]] = []


def check(label: str, passed: bool, detail: str = "") -> None:
    results.append((label, passed, detail))


# 1 - identical arguments repeated is the classic loop signature
g = make_guard(max_identical_calls=2)
args = {"genre": "Drama", "limit": 6}
first = g.before_tool(tool("find_comparable_titles"), dict(args), None)
second = g.before_tool(tool("find_comparable_titles"), dict(args), None)
third = g.before_tool(tool("find_comparable_titles"), dict(args), None)
check("identical-call loop blocked on 3rd attempt",
      first is None and second is None and third is not None,
      str(third.get("error", ""))[:80] if third else "not blocked")

# ...but a different argument set is still allowed
fourth = g.before_tool(tool("find_comparable_titles"), {"genre": "Horror", "limit": 6}, None)
check("different arguments still allowed after a loop trip", fourth is None)

# 2 - one tool hammered with varying arguments
g = make_guard(max_calls_per_tool=3)
outcomes = [g.before_tool(tool("query_catalogue"), {"sql": f"SELECT {i}"}, None) for i in range(5)]
blocked_at = next((i for i, o in enumerate(outcomes) if o is not None), None)
check("per-tool ceiling stops the 4th call", blocked_at == 3, f"blocked at index {blocked_at}")

# 3 - total tool budget across different tools
g = make_guard(max_tool_calls=4, max_calls_per_tool=99, max_identical_calls=99)
names = ["a", "b", "c", "d", "e", "f"]
outcomes = [g.before_tool(tool(n), {"i": i}, None) for i, n in enumerate(names)]
blocked_at = next((i for i, o in enumerate(outcomes) if o is not None), None)
check("total tool ceiling stops the 5th call", blocked_at == 4, f"blocked at index {blocked_at}")

# 4 - model call ceiling
g = make_guard(max_model_calls=3)
model_outcomes = [g.before_model(None, None) for _ in range(5)]
blocked_at = next((i for i, o in enumerate(model_outcomes) if o is not None), None)
check("model call ceiling stops the 4th call", blocked_at == 3, f"blocked at index {blocked_at}")

# 5 - cost ceiling, driven by the real meter
g = make_guard(max_cost_usd=0.01)
try:
    g.ctx.meter.record("gemini-2.5-flash", 200_000, 0)   # ~$0.06, over the 0.01 ceiling
except Exception:
    pass  # the meter raises; the guard must also refuse independently
blocked = g.before_tool(tool("benchmark_segment"), {}, None)
check("cost ceiling refuses further tool calls", blocked is not None,
      str(blocked.get("error", ""))[:80] if blocked else "not blocked")

# 6 - wall clock
g = make_guard(deadline_seconds=0)
time.sleep(0.01)
blocked_tool = g.before_tool(tool("describe_catalogue"), {}, None)
blocked_model = g.before_model(None, None)
check("deadline blocks both tools and model calls",
      blocked_tool is not None and blocked_model is not None)

# 7 - a tripped guard tells the model what to do instead of just erroring
g = make_guard(max_tool_calls=0)
payload = g.before_tool(tool("find_comparable_titles"), {}, None)
check("trip payload instructs the model to summarise",
      payload is not None and "Summarise" in payload.get("instruction", ""),
      payload.get("instruction", "") if payload else "")

# 8 - trips are recorded in the request trace, not swallowed
check("trip recorded in trace for the UI",
      any(t.tool == "guardrail" for t in g.ctx.trace),
      f"{len(g.ctx.trace)} trace entries")

# 9 - strict profile is genuinely tighter
strict, normal = Limits.strict(), Limits()
check("strict profile is tighter on every axis",
      strict.max_model_calls < normal.max_model_calls
      and strict.max_tool_calls < normal.max_tool_calls
      and strict.deadline_seconds < normal.deadline_seconds)


width = max(len(label) for label, _, _ in results)
failures = 0
for label, passed, detail in results:
    mark = "PASS" if passed else "FAIL"
    if not passed:
        failures += 1
    line = f"{mark}  {label.ljust(width)}"
    if detail:
        line += f"   | {detail}"
    print(line)

print(f"\n{len(results) - failures}/{len(results)} passed")
sys.exit(1 if failures else 0)
