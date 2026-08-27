"""Single place where the Gemini client is constructed.

Corporate TLS inspection (Norton) breaks certifi-based verification on this
developer's machine, so truststore is injected when available. It is a no-op
on Cloud Run.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings

logger = logging.getLogger("greenlight.llm")

try:  # pragma: no cover - environment dependent
    import truststore

    truststore.inject_into_ssl()
    logger.debug("truststore injected into ssl")
except Exception:  # noqa: BLE001
    pass


@lru_cache(maxsize=1)
def genai_client():
    from google import genai

    s = settings()
    if s.use_vertex:
        if not s.gcp_project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT must be set when using Vertex AI.")
        client = genai.Client(vertexai=True, project=s.gcp_project, location=s.gcp_location)
        logger.info("Gemini client: Vertex AI project=%s location=%s", s.gcp_project, s.gcp_location)
    else:
        if not s.gemini_api_key:
            raise RuntimeError("GOOGLE_API_KEY must be set when not using Vertex AI.")
        client = genai.Client(api_key=s.gemini_api_key)
        logger.info("Gemini client: Developer API")
    return client


@lru_cache(maxsize=1)
def adk_model_class():
    """ADK model class that reuses our one warmed genai client.

    Handing ADK a plain model string makes it build its own client, and that
    client pays the TLS handshake and token fetch again on every call. Measured
    on this machine: 30-40s per call that way, 0.7s once a single client is
    shared. ADK documents overriding ``api_client`` for exactly this.
    """
    from functools import cached_property

    from google.adk.models import Gemini

    class SharedClientGemini(Gemini):
        @cached_property
        def api_client(self):
            return genai_client()

    return SharedClientGemini


def adk_model(model_name: str):
    return adk_model_class()(model=model_name)


def warm_up() -> float:
    """Pay the cold-start cost once, at import or startup, not mid-request."""
    import time

    started = time.perf_counter()
    try:
        client = genai_client()
        client.models.count_tokens(model=settings().reasoning_model, contents="warm")
    except Exception as exc:  # noqa: BLE001
        logger.warning("warm-up call failed (continuing): %s", exc)
    elapsed = time.perf_counter() - started
    logger.info("Gemini client warmed in %.1fs", elapsed)
    return elapsed


def llm_status() -> dict:
    s = settings()
    return {
        "backend": "vertex-ai" if s.use_vertex else "gemini-api",
        "project": s.gcp_project or None,
        "location": s.gcp_location,
        "reasoning_model": s.reasoning_model,
        "writer_model": s.writer_model,
        "embedding_model": s.embedding_model,
        "embedding_dim": s.embedding_dim,
        "configured": s.gemini_configured,
    }
