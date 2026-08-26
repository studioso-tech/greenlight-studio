"""Gemini embeddings for screenplay / logline similarity.

The corpus side (TMDB overviews) and the query side (a screenplay logline) are
the same kind of text, so both use SEMANTIC_SIMILARITY rather than the
asymmetric retrieval task types.

gemini-embedding-001 is Matryoshka-trained. Truncating 3072 -> 768 keeps most
of the quality at a quarter of the ClickHouse storage and cosineDistance cost,
but the truncated vector must be re-normalised by hand.
"""
from __future__ import annotations

import logging
import math
from typing import Iterable, Optional, Sequence

from app.config import settings
from app.cost import CostMeter
from app.llm import genai_client

logger = logging.getLogger("greenlight.embeddings")

TASK_TYPE = "SEMANTIC_SIMILARITY"
# The API rejects oversized batches; 32 is comfortably inside the limit.
BATCH_SIZE = 32


def _l2_normalise(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return list(vec)
    return [v / norm for v in vec]


def embed_texts(
    texts: Sequence[str],
    *,
    meter: Optional[CostMeter] = None,
    batch_size: int = BATCH_SIZE,
) -> list[list[float]]:
    """Embed a batch of texts, returning unit-length vectors of embedding_dim."""
    s = settings()
    client = genai_client()
    out: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        chunk = [t if t.strip() else "(no synopsis provided)" for t in texts[start : start + batch_size]]
        response = client.models.embed_content(
            model=s.embedding_model,
            contents=chunk,
            config={
                "task_type": TASK_TYPE,
                "output_dimensionality": s.embedding_dim,
            },
        )
        for emb in response.embeddings:
            out.append(_l2_normalise(emb.values))

        if meter is not None:
            billed = getattr(response, "metadata", None)
            token_count = getattr(billed, "billable_character_count", None)
            if token_count is None:
                # Character-count billing is not always returned; approximate
                # from text length so the ceiling still moves.
                token_count = sum(len(t) for t in chunk) // 4
            meter.record(s.embedding_model, int(token_count), 0)

        logger.debug("embedded %d/%d", min(start + batch_size, len(texts)), len(texts))

    return out


def embed_one(text: str, *, meter: Optional[CostMeter] = None) -> list[float]:
    return embed_texts([text], meter=meter)[0]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
