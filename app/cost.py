"""Token accounting and hard per-request cost ceiling.

Two jobs:
  1. Log what every model call cost (COST lines, greppable in Cloud Logging).
  2. Stop a runaway agent loop before it spends real money.

PRICES MUST BE UPDATED WHENEVER A MODEL IS SWAPPED. Vertex AI list price,
USD per 1,000,000 tokens. Verified against cloud.google.com/vertex-ai/pricing
on 2026-08-27.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Dict

from app.config import settings

logger = logging.getLogger("greenlight.cost")


@dataclass(frozen=True)
class Price:
    input_per_m: float
    output_per_m: float


PRICES: Dict[str, Price] = {
    "gemini-2.5-flash": Price(input_per_m=0.30, output_per_m=2.50),
    "gemini-2.5-flash-lite": Price(input_per_m=0.10, output_per_m=0.40),
    "gemini-2.5-pro": Price(input_per_m=1.25, output_per_m=10.00),
    "gemini-embedding-001": Price(input_per_m=0.15, output_per_m=0.0),
}
_UNKNOWN = Price(input_per_m=2.00, output_per_m=12.00)  # deliberately pessimistic


class BudgetExceeded(RuntimeError):
    """Raised when a single request would exceed its cost ceiling."""


@dataclass
class CostMeter:
    """Per-request meter. One instance per inbound API call."""

    request_id: str
    ceiling_usd: float = field(default_factory=lambda: settings().max_cost_usd_per_request)
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, model: str, input_tokens: int, output_tokens: int = 0) -> float:
        price = PRICES.get(model, _UNKNOWN)
        if model not in PRICES:
            logger.warning("COST unknown-model=%s falling back to pessimistic price", model)
        delta = (input_tokens / 1_000_000) * price.input_per_m + (
            output_tokens / 1_000_000
        ) * price.output_per_m
        with self._lock:
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.usd += delta
            self.calls += 1
            total = self.usd
        logger.info(
            "COST request=%s model=%s in=%d out=%d call_usd=%.6f total_usd=%.6f ceiling=%.4f",
            self.request_id, model, input_tokens, output_tokens, delta, total, self.ceiling_usd,
        )
        if total > self.ceiling_usd:
            logger.error(
                "COST CEILING EXCEEDED request=%s total_usd=%.6f ceiling=%.4f calls=%d",
                self.request_id, total, self.ceiling_usd, self.calls,
            )
            raise BudgetExceeded(
                f"Request {self.request_id} exceeded its ${self.ceiling_usd:.2f} ceiling "
                f"(spent ${total:.4f} across {self.calls} model calls)."
            )
        return delta

    def check(self) -> None:
        """Call before an expensive step; fails fast if already at the ceiling."""
        if self.usd > self.ceiling_usd:
            raise BudgetExceeded(
                f"Request {self.request_id} already spent ${self.usd:.4f}."
            )

    def summary(self) -> dict:
        return {
            "model_calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": round(self.usd, 6),
            "ceiling_usd": self.ceiling_usd,
        }
