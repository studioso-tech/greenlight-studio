# Greenlight Studio — single image, no build step for the front end.
#
# Dependencies are fully pinned (requirements.txt is a freeze of a clean
# resolve, not a wish list). A transitive package updating itself between the
# build that worked and the build on submission day is not a risk worth taking.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# Dependencies first, so source edits do not invalidate the layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application source.
COPY app ./app
COPY web ./web
COPY ingest ./ingest

# The catalogue itself lives in ClickHouse, not in the image. Only the two
# small files the UI needs are copied: the sample material a judge can click,
# and the manifest that records exactly what coverage may be claimed.
COPY data/samples.json data/manifest.json ./data/

# The official ClickHouse MCP server pins fastmcp 2.x while the app runs 3.x,
# so it gets its own environment rather than dragging the app backwards.
RUN python -m venv /opt/mcp-clickhouse \
    && /opt/mcp-clickhouse/bin/pip install --no-cache-dir "mcp-clickhouse==0.4.1"
ENV GREENLIGHT_MCP_PYTHON=/opt/mcp-clickhouse/bin/python

# Run as a non-root user.
RUN useradd --create-home --uid 10001 greenlight \
    && chown -R greenlight:greenlight /app
USER greenlight

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
