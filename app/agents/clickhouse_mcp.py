"""The official ClickHouse MCP server, wired into ADK.

The ClickHouse track requires that the project "actively use ClickHouse at
runtime via the official ClickHouse MCP server (mcp-clickhouse)". This module
launches that server as a child process over stdio and hands ADK its tools, so
the agent really is talking to ClickHouse through the partner's own server -
not through a reimplementation of it.

mcp-clickhouse pins fastmcp 2.x while this project runs 3.x, so the server
lives in its own virtualenv (.venv-mcp). Installing it alongside the app
downgrades fastmcp and leaves a mixed, broken install; keeping the processes
separate is both the correct MCP architecture and the thing that stops the
dependency fight.

The server exposes list_databases / list_tables / run_query. Our own
app/store/queries.py tools stay alongside it: they carry the vector search and
the measured latency the UI needs, which the generic server has no notion of.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger("greenlight.clickhouse_mcp")

ROOT = Path(__file__).resolve().parents[2]
MCP_VENV = ROOT / ".venv-mcp"

# Read-only tools only. The server can be told to allow writes; we never do.
ALLOWED_TOOLS = ["list_databases", "list_tables", "run_query"]


def mcp_python() -> Optional[Path]:
    """Interpreter for the isolated MCP environment, if it was created.

    In the container the environment lives outside the project, so the path is
    passed in; locally it sits in .venv-mcp next to the source.
    """
    override = os.getenv("GREENLIGHT_MCP_PYTHON")
    if override and Path(override).exists():
        return Path(override)
    for candidate in (MCP_VENV / "Scripts" / "python.exe", MCP_VENV / "bin" / "python"):
        if candidate.exists():
            return candidate
    return None


def server_env() -> dict[str, str]:
    """Environment for the child process.

    Passed through the process environment rather than the command line so the
    password never lands in a process listing.
    """
    s = settings()
    env = {
        "CLICKHOUSE_HOST": s.ch_host,
        "CLICKHOUSE_PORT": str(s.ch_port),
        "CLICKHOUSE_USER": s.ch_user,
        "CLICKHOUSE_PASSWORD": s.ch_password,
        "CLICKHOUSE_DATABASE": s.ch_database,
        "CLICKHOUSE_SECURE": "true" if s.ch_secure else "false",
        "CLICKHOUSE_VERIFY": "true" if s.ch_secure else "false",
        # Belt and braces: the SQL guard in app/store/queries.py refuses writes
        # on our side, and the server refuses them on its own.
        "CLICKHOUSE_ALLOW_WRITE_ACCESS": "false",
        "CLICKHOUSE_ALLOW_DROP": "false",
        "CLICKHOUSE_ENABLED": "true",
        "CLICKHOUSE_CONNECT_TIMEOUT": str(s.ch_connect_timeout),
        "CLICKHOUSE_SEND_RECEIVE_TIMEOUT": str(s.ch_query_timeout),
        # chDB is a second, unrelated backend the server also offers. Off.
        "CHDB_ENABLED": "false",
    }
    # Windows needs SYSTEMROOT/PATH present or the child cannot start.
    for passthrough in ("SYSTEMROOT", "PATH", "TEMP", "TMP", "APPDATA"):
        if passthrough in os.environ:
            env[passthrough] = os.environ[passthrough]
    return env


_TOOLSET = None
_TOOLSET_TRIED = False


def shared_toolset():
    """One MCP server process for the whole app, not one per request.

    ADK keeps the stdio session alive on the toolset, so building it per agent
    would spawn a child process on every analysis. Cached, and a failure to
    start is logged once and then degrades to the direct client rather than
    failing the request.
    """
    global _TOOLSET, _TOOLSET_TRIED
    if _TOOLSET_TRIED:
        return _TOOLSET
    _TOOLSET_TRIED = True
    try:
        _TOOLSET = build_toolset()
    except Exception as exc:  # noqa: BLE001
        logger.error("could not start mcp-clickhouse: %s", exc)
        _TOOLSET = None
    return _TOOLSET


def build_toolset(tool_filter: Optional[list[str]] = None):
    """ADK toolset backed by the official server. None if it is not installed."""
    python = mcp_python()
    if python is None:
        logger.warning(
            "mcp-clickhouse is not installed. Create it with:\n"
            "  python -m venv .venv-mcp\n"
            "  .venv-mcp/Scripts/pip install mcp-clickhouse==0.4.1"
        )
        return None
    if not settings().clickhouse_configured:
        logger.warning("CLICKHOUSE_HOST is unset; not starting the MCP server.")
        return None

    from google.adk.tools import McpToolset
    from google.adk.tools.mcp_tool import StdioConnectionParams
    from mcp import StdioServerParameters

    params = StdioServerParameters(
        command=str(python),
        args=["-m", "mcp_clickhouse.main"],
        env=server_env(),
    )
    logger.info("starting official mcp-clickhouse via %s", python)
    return McpToolset(
        connection_params=StdioConnectionParams(server_params=params, timeout=30.0),
        tool_filter=tool_filter or ALLOWED_TOOLS,
        tool_name_prefix="clickhouse",
    )


def describe() -> dict:
    """Reported on /api/health so the integration is visible, not asserted.

    This used to report installed=True whenever the interpreter existed on
    disk, and it did exactly that while the app could not import `mcp` and was
    silently falling back to the direct client. Health that reports wiring
    which is not wired is worse than no health check, so this now says whether
    the toolset actually built.
    """
    python = mcp_python()
    if python is None:
        return {
            "official_server": "mcp-clickhouse",
            "status": "not installed",
            "usable": False,
            "interpreter": None,
            "detail": "no interpreter found for the MCP environment",
        }

    # shared_toolset() caches, so this is the same object the agents get - not
    # a separate probe that could succeed where the real path fails.
    try:
        import mcp  # noqa: F401
    except ImportError as exc:
        return {
            "official_server": "mcp-clickhouse",
            "status": "unusable",
            "usable": False,
            "interpreter": str(python),
            "detail": f"the app cannot speak MCP: {exc}",
        }

    toolset = shared_toolset()
    return {
        "official_server": "mcp-clickhouse",
        "status": "ready" if toolset is not None else "failed to start",
        "usable": toolset is not None,
        "interpreter": str(python),
        "tools": ALLOWED_TOOLS,
        "write_access": False,
        "detail": None if toolset is not None else "see logs for the startup error",
    }
