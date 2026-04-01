from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent import nodes
from app.agent.nodes import _record_drink_impl, _search_menu_impl, get_mcp_tools, llm_node, route_after_llm, route_after_tool
from app.api import agent as agent_api
from app.core.config import get_settings
from app.tools.mcp_server import _guard_actor


class _FakeChunk:
    def __init__(self, content):
        self.content = content


class _FakeMessage:
    def __init__(self, tool_calls=None):
        self.tool_calls = tool_calls or []


class _FakeLLM:
    def __init__(self):
        self.last_messages = None

    async def ainvoke(self, messages):
        self.last_messages = messages
        return SimpleNamespace(content="这是模型回复", tool_calls=[])


def _build_client(user_id: str = "u-test") -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def add_user_id(request, call_next):
        request.state.user_id = user_id
        request.state.request_id = f"req-{user_id}"
        return await call_next(request)

    app.include_router(agent_api.router)
    return TestClient(app)


def test_agent_chat_text_stream(monkeypatch):
    seen = {}

    async def _fake_stream(**_kwargs) -> AsyncGenerator[dict, None]:
        seen.update(_kwargs)
        yield {"event": "on_chat_model_stream", "data": {"chunk": _FakeChunk("你好")}}

    monkeypatch.setattr(agent_api, "stream_agent_events", _fake_stream)
    client = _build_client()

    with client.stream(
        "POST",
        "/bobo/agent/chat",
        json={"message": "hi", "thread_id": "thread-1", "user_id": "body-user"},
    ) as resp:
        body = "".join(resp.iter_text())

    assert resp.status_code == 200
    assert '"type": "meta"' in body
    assert '"request_id": "req-u-test"' in body
    assert '"type": "text"' in body
    assert '"content": "你好"' in body
    assert '"type": "done"' in body
    assert seen["user_id"] == "u-test"
    assert seen["thread_id"] == "user-u-test:session-thread-1"
    assert seen["request_id"]


def test_agent_chat_tool_call_stream(monkeypatch):
    async def _fake_stream(**_kwargs) -> AsyncGenerator[dict, None]:
        yield {
            "event": "on_tool_start",
            "name": "search_menu",
            "data": {"input": {"query": "多肉葡萄"}},
        }

    monkeypatch.setattr(agent_api, "stream_agent_events", _fake_stream)
    client = _build_client()

    with client.stream(
        "POST",
        "/bobo/agent/chat",
        json={"message": "推荐喜茶", "thread_id": "session-2"},
    ) as resp:
        body = "".join(resp.iter_text())

    assert resp.status_code == 200
    assert '"type": "tool_call"' in body
    assert '"tool": "search_menu"' in body
    assert '"query": "多肉葡萄"' in body
    assert '"type": "done"' in body


def test_agent_max_steps_router_limit():
    assert route_after_tool({"max_steps": 0}) == "end"
    assert route_after_tool({"max_steps": 1}) == "llm"

    state_with_tool = {
        "max_steps": 2,
        "messages": [_FakeMessage(tool_calls=[{"name": "search_menu", "args": {}}])],
    }
    assert route_after_llm(state_with_tool) == "tool"

    state_limit_reached = {
        "max_steps": 0,
        "messages": [_FakeMessage(tool_calls=[{"name": "search_menu", "args": {}}])],
    }
    assert route_after_llm(state_limit_reached) == "end"


def test_llm_node_with_mocked_qwen_response():
    llm = _FakeLLM()
    runtime = {"llm": llm}
    state = {"messages": [("user", "今天喝了什么推荐")] , "max_steps": 10}

    result = __import__("asyncio").run(llm_node(state, runtime))

    assert "messages" in result
    assert result["messages"][0].content == "这是模型回复"
    assert llm.last_messages[0][0] == "system"


def test_record_drink_impl_uses_real_user_id(monkeypatch):
    captured = {}

    def _fake_insert_records(user_id, items):
        captured["user_id"] = user_id
        captured["items"] = items
        return [{"id": "r1"}]

    monkeypatch.setattr("app.tooling.operations.insert_records", _fake_insert_records)

    result = _record_drink_impl(
        brand="喜茶",
        name="多肉葡萄",
        user_id="real-user",
    )

    assert result["ok"] is True
    assert captured["user_id"] == "real-user"
    assert captured["items"][0]["brand"] == "喜茶"


def test_record_drink_impl_requires_authenticated_user():
    with pytest.raises(PermissionError):
        _record_drink_impl(brand="喜茶", name="多肉葡萄")


def test_search_menu_impl_calls_real_search(monkeypatch):
    class _FakeService:
        async def search(self, query, brand=None, top_k=5):
            captured["query"] = query
            captured["brand"] = brand
            captured["top_k"] = top_k
            return [{"id": "m1", "brand": "喜茶", "name": "多肉葡萄"}]

    captured = {}
    monkeypatch.setattr("app.tooling.operations.QdrantService", lambda: _FakeService())

    result = __import__("asyncio").run(_search_menu_impl(query="葡萄", brand="喜茶", user_id="real-user"))

    assert result["results"][0]["name"] == "多肉葡萄"
    assert captured == {"query": "葡萄", "brand": "喜茶", "top_k": 5}


def test_mcp_guard_requires_user_identity():
    with pytest.raises(PermissionError):
        _guard_actor(None)


def test_agent_tool_mode_defaults_to_hybrid_debug_in_dev(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.delenv("AGENT_TOOL_MODE", raising=False)

    assert nodes._agent_tool_mode() == "hybrid_debug"

    get_settings.cache_clear()


def test_get_mcp_tools_uses_local_mode(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_TOOL_MODE", "local_fallback")

    tools = __import__("asyncio").run(get_mcp_tools())

    assert {tool.name for tool in tools} >= {"record_drink", "search_menu", "get_stats", "get_calendar", "update_menu"}


def test_get_mcp_tools_passes_auth_headers(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_TOOL_MODE", "mcp_remote")
    monkeypatch.setenv("JWT_SECRET", "header-secret")
    monkeypatch.delenv("MCP_SERVICE_TOKEN", raising=False)

    captured = {}

    class _FakeClient:
        def __init__(self, config):
            captured["config"] = config

        async def get_tools(self):
            return [SimpleNamespace(name="search_menu")]

    fake_client_module = types.SimpleNamespace(MultiServerMCPClient=_FakeClient)
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters", types.SimpleNamespace(client=fake_client_module))
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", fake_client_module)
    monkeypatch.setattr(nodes, "get_agent_context", lambda: {"request_id": "req-123", "thread_id": "thread-123", "source": "agent"})

    tools = __import__("asyncio").run(get_mcp_tools())

    assert tools[0].name == "search_menu"
    assert "/mcp/mcp" in captured["config"]["bobo"]["url"]
    headers = captured["config"]["bobo"]["headers"]
    assert headers["Authorization"] == "Bearer header-secret:mcp"
    assert headers["X-Bobo-Source"] == "agent"
    assert headers["X-Request-Id"] == "req-123"
    assert headers["X-Bobo-Thread-Id"] == "thread-123"


def test_get_mcp_tools_hybrid_falls_back_when_mcp_unavailable(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_TOOL_MODE", "hybrid_debug")

    class _BrokenClient:
        def __init__(self, _config):
            pass

        async def get_tools(self):
            raise RuntimeError("boom")

    fake_client_module = types.SimpleNamespace(MultiServerMCPClient=_BrokenClient)
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters", types.SimpleNamespace(client=fake_client_module))
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", fake_client_module)

    tools = __import__("asyncio").run(get_mcp_tools())

    assert any(tool.name == "record_drink" for tool in tools)


def test_get_mcp_tools_remote_mode_raises_when_mcp_unavailable(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_TOOL_MODE", "mcp_remote")

    class _BrokenClient:
        def __init__(self, _config):
            pass

        async def get_tools(self):
            raise RuntimeError("boom")

    fake_client_module = types.SimpleNamespace(MultiServerMCPClient=_BrokenClient)
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters", types.SimpleNamespace(client=fake_client_module))
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", fake_client_module)

    with pytest.raises(RuntimeError, match="boom"):
        __import__("asyncio").run(get_mcp_tools())

    get_settings.cache_clear()
