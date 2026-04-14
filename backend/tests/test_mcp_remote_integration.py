from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import threading
import time
from unittest.mock import patch

import pytest
import uvicorn
from langchain_core.tools.base import ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient

os.environ["JWT_SECRET"] = "test-secret"
os.environ["MCP_SERVICE_TOKEN"] = "mcp-test-token"
os.environ["DATABASE_URL"] = ""

from app.core.config import get_settings  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.models.db import close_pool  # noqa: E402


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _wait_for_port(port: int, timeout_s: float = 10.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"server on port {port} did not start")


def _extract_json_payload(blocks: list[dict]) -> dict:
    text_block = next(block for block in blocks if block.get("type") == "text")
    return json.loads(text_block["text"])


@pytest.fixture()
def remote_mcp_url():
    close_pool()
    get_settings.cache_clear()
    mcp_server_module = importlib.reload(importlib.import_module("app.tools.mcp_server"))
    main_module = importlib.reload(importlib.import_module("app.main"))
    main_module.mcp_container = mcp_server_module.create_mcp_server()
    port = _free_port()
    app = main_module.create_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_for_port(port)

    try:
        yield f"http://127.0.0.1:{port}/mcp/"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        close_pool()
        get_settings.cache_clear()


async def _call_remote_tool(url: str, headers: dict[str, str], tool_name: str, payload: dict) -> list[dict]:
    client = MultiServerMCPClient(
        {
            "bobo": {
                "transport": "http",
                "url": url,
                "headers": headers,
            }
        }
    )
    tools = await client.get_tools()
    tool = next(tool for tool in tools if tool.name == tool_name)
    return await tool.ainvoke(payload)


def test_remote_mcp_search_menu_roundtrip_with_service_token(remote_mcp_url):
    class _FakeSearchService:
        async def search(self, query, brand=None, top_k=5, source="pytest"):
            return [{"id": "m1", "brand": brand or "喜茶", "name": "多肉葡萄", "score": 0.99}]

    with patch("app.tooling.operations.get_menu_search_service", lambda: _FakeSearchService()):
        blocks = asyncio.run(
            _call_remote_tool(
                remote_mcp_url,
                {"Authorization": "Bearer mcp-test-token", "X-Bobo-Source": "pytest"},
                "search_menu",
                {"query": "葡萄", "brand": "喜茶", "user_id": "u-remote"},
            )
        )

    payload = _extract_json_payload(blocks)
    assert payload["query"] == "葡萄"
    assert payload["results"][0]["name"] == "多肉葡萄"


def test_remote_mcp_search_menu_uses_user_token_identity(remote_mcp_url):
    class _FakeSearchService:
        async def search(self, query, brand=None, top_k=5, source="pytest"):
            return [{"id": "m2", "brand": brand or "喜茶", "name": "芝芝莓莓", "score": 0.95}]

    access_token = create_access_token("u-user")

    with patch("app.tooling.operations.get_menu_search_service", lambda: _FakeSearchService()):
        blocks = asyncio.run(
            _call_remote_tool(
                remote_mcp_url,
                {"Authorization": f"Bearer {access_token}", "X-Bobo-Source": "pytest"},
                "search_menu",
                {"query": "莓莓", "brand": "喜茶"},
            )
        )

    payload = _extract_json_payload(blocks)
    assert payload["results"][0]["name"] == "芝芝莓莓"


def test_remote_mcp_update_menu_requires_admin_capability(remote_mcp_url):
    access_token = create_access_token("u-user")

    with pytest.raises(ToolException, match="missing capability for tool:update_menu"):
        asyncio.run(
            _call_remote_tool(
                remote_mcp_url,
                {"Authorization": f"Bearer {access_token}", "X-Bobo-Source": "pytest"},
                "update_menu",
                {"action": "delete", "item": {"id": "m1"}},
            )
        )


def test_remote_mcp_update_menu_allows_service_token(remote_mcp_url):
    class _FakeMenuOps:
        async def apply_action(self, action, item):
            return {
                "ok": True,
                "action": action,
                "menu_id": item.get("id", "m1"),
                "db_updated": True,
                "vector_updated": True,
                "warnings": [],
            }

    with patch("app.tooling.operations.get_menu_ops_service", lambda: _FakeMenuOps()):
        blocks = asyncio.run(
            _call_remote_tool(
                remote_mcp_url,
                {"Authorization": "Bearer mcp-test-token", "X-Bobo-Source": "pytest"},
                "update_menu",
                {"action": "delete", "item": {"id": "m1"}, "user_id": "u-admin"},
            )
        )

    payload = _extract_json_payload(blocks)
    assert payload["ok"] is True
    assert payload["action"] == "delete"
