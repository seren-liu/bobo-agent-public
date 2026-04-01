from __future__ import annotations

import logging
from typing import Any

from app.tooling import guard_actor as _guard_actor
from app.tooling import register_mcp_tools

try:
    from fastmcp import FastMCP
except Exception:  # pragma: no cover
    FastMCP = None


logger = logging.getLogger("bobo.mcp")


class MCPContainer:
    def __init__(self, server: Any, http_app: Any):
        self.server = server
        self.http_app = http_app


def create_mcp_server() -> MCPContainer | None:
    if FastMCP is None:
        return None

    try:
        mcp = FastMCP("bobo-tools")
    except Exception:
        return None

    register_mcp_tools(mcp)
    logger.info("registered MCP tools from app.tooling.registry")

    http_app = None
    try:
        if hasattr(mcp, "streamable_http_app"):
            try:
                http_app = mcp.streamable_http_app(path="/")
            except TypeError:
                http_app = mcp.streamable_http_app()
        elif hasattr(mcp, "http_app"):
            try:
                http_app = mcp.http_app(path="/")
            except TypeError:
                http_app = mcp.http_app()
    except Exception:
        http_app = None

    return MCPContainer(server=mcp, http_app=http_app)


mcp_container = create_mcp_server()
