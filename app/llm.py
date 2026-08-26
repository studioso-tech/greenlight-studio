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
