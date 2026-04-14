from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import date
from types import SimpleNamespace
import json
import sys
import types
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent import nodes
from app.agent.nodes import _record_drink_impl, _search_menu_impl, get_mcp_tools, llm_node, route_after_llm, route_after_tool, tool_node
from app.api import agent as agent_api
from app.core.config import get_settings
from app.core.rate_limit import clear_rate_limits
from app.services import online_menu_search
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


def setup_function():
    clear_rate_limits()


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


def test_session_thread_id_rewrites_cross_user_prefix():
    assert agent_api._session_thread_id("u-safe", "user-u-victim:session-shared") == "user-u-safe:session-shared"


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


def test_agent_chat_rejects_when_daily_budget_exhausted(monkeypatch):
    monkeypatch.setattr(
        agent_api,
        "_daily_budget_snapshot",
        lambda **_kwargs: {
            "remaining_cny": 0.0,
            "remaining_output_tokens": 0,
            "spent_cost_cny": 1.0,
            "budget_cny": 1.0,
            "pricing": SimpleNamespace(model="qwen3-32b", input_price_per_million=2.0, output_price_per_million=8.0),
        },
    )
    client = _build_client()

    resp = client.post(
        "/bobo/agent/chat",
        json={"message": "hi", "thread_id": "thread-budget"},
    )

    assert resp.status_code == 429
    assert "预算已用完" in resp.text


def test_agent_chat_rate_limit(monkeypatch):
    async def _fake_stream(**_kwargs) -> AsyncGenerator[dict, None]:
        yield {"event": "on_chat_model_stream", "data": {"chunk": _FakeChunk("你好")}}

    calls = {"count": 0}

    def _fake_limit(**kwargs):
        calls["count"] += 1
        if calls["count"] > 1:
            raise agent_api.HTTPException(status_code=429, detail="too many requests")

    monkeypatch.setattr(agent_api, "enforce_rate_limit", _fake_limit)
    monkeypatch.setattr(agent_api, "stream_agent_events", _fake_stream)
    client = _build_client()

    resp1 = client.post("/bobo/agent/chat", json={"message": "hi", "thread_id": "thread-rate-1"})
    resp2 = client.post("/bobo/agent/chat", json={"message": "hi again", "thread_id": "thread-rate-2"})

    assert resp1.status_code == 200
    assert resp2.status_code == 429


def test_agent_chat_records_daily_usage(monkeypatch):
    captured = {}

    async def _fake_stream(**_kwargs) -> AsyncGenerator[dict, None]:
        yield {"event": "on_chat_model_stream", "data": {"chunk": _FakeChunk("你好")}}
        yield {
            "event": "on_chat_model_end",
            "data": {"output": {"usage_metadata": {"input_tokens": 120, "output_tokens": 45}}},
        }

    monkeypatch.setattr(agent_api, "stream_agent_events", _fake_stream)
    monkeypatch.setattr(
        agent_api,
        "_daily_budget_snapshot",
        lambda **_kwargs: {
            "usage_date": date(2026, 4, 4),
            "remaining_cny": 1.0,
            "remaining_output_tokens": 100000,
            "spent_cost_cny": 0.0,
            "budget_cny": 1.0,
            "pricing": SimpleNamespace(model="qwen3-32b", input_price_per_million=2.0, output_price_per_million=8.0),
        },
    )
    monkeypatch.setattr(
        agent_api.repository,
        "add_daily_llm_usage",
        lambda **kwargs: captured.update(kwargs) or kwargs,
    )
    client = _build_client()

    with client.stream(
        "POST",
        "/bobo/agent/chat",
        json={"message": "hi", "thread_id": "thread-usage"},
    ) as resp:
        body = "".join(resp.iter_text())

    assert resp.status_code == 200
    assert '"type": "done"' in body
    assert captured["model"] == "qwen3-32b"
    assert captured["input_tokens"] == 120
    assert captured["output_tokens"] == 45


def test_agent_chat_stats_fast_path(monkeypatch):
    monkeypatch.setattr(
        agent_api,
        "get_stats_impl",
        lambda **_kwargs: {
            "total_amount": 58.0,
            "total_count": 4,
            "brand_dist": [{"brand": "一点点", "count": 2}],
            "weekly_trend": [],
            "sugar_pref": [],
            "ice_pref": [],
            "daily_density": {},
        },
    )
    client = _build_client()

    with client.stream(
        "POST",
        "/bobo/agent/chat",
        json={"message": "这周我喝了多少杯", "thread_id": "stats-1"},
    ) as resp:
        body = "".join(resp.iter_text())

    assert resp.status_code == 200
    assert "这周你一共喝了 4 杯" in body
    assert '"type": "done"' in body


def test_agent_chat_recent_records_fast_path(monkeypatch):
    captured = {}

    def _fake_recent(**kwargs):
        captured.update(kwargs)
        return {
            "records": [
                {"brand": "喜茶", "name": "多肉葡萄", "price": 19, "consumed_at": "2026-04-04T12:00:00"}
            ]
        }

    monkeypatch.setattr(
        agent_api,
        "get_recent_records_impl",
        _fake_recent,
    )
    client = _build_client()

    with client.stream(
        "POST",
        "/bobo/agent/chat",
        json={"message": "我上次喝了什么", "thread_id": "recent-1"},
    ) as resp:
        body = "".join(resp.iter_text())

    assert resp.status_code == 200
    assert "你最近一杯是 2026-04-04 的 喜茶 多肉葡萄，¥19。" in body
    assert '"type": "done"' in body
    assert captured["limit"] == 1


def test_agent_chat_day_records_fast_path(monkeypatch):
    monkeypatch.setattr(
        agent_api,
        "get_day_impl",
        lambda **_kwargs: {
            "records": [
                {"brand": "喜茶", "name": "多肉葡萄", "price": 19},
                {"brand": "一点点", "name": "葡萄柚绿", "price": 18},
            ]
        },
    )
    client = _build_client()

    with client.stream(
        "POST",
        "/bobo/agent/chat",
        json={"message": "今天喝了什么", "thread_id": "day-1"},
    ) as resp:
        body = "".join(resp.iter_text())

    assert resp.status_code == 200
    assert "今天你喝了 2 杯" in body
    assert "喜茶 多肉葡萄" in body
    assert "一点点 葡萄柚绿" in body


def test_agent_chat_menu_fast_path_uses_multi_query_and_rerank(monkeypatch):
    calls: list[str] = []

    async def _fake_search_menu_impl(**kwargs):
        query = kwargs["query"]
        calls.append(query)
        if query == "厚乳":
            return {
                "results": [
                    {"id": "m-1", "brand": "古茗", "name": "经典奶香奶茶", "price": 12, "description": "经典奶香", "score": 0.52}
                ]
            }
        return {"results": []}

    monkeypatch.setattr(agent_api, "search_menu_impl", _fake_search_menu_impl)
    client = _build_client()

    with client.stream(
        "POST",
        "/bobo/agent/chat",
        json={"message": "给我推荐古茗的经典奶茶top3", "thread_id": "menu-1"},
    ) as resp:
        body = "".join(resp.iter_text())

    assert resp.status_code == 200
    assert "经典奶香奶茶" in body
    assert "古茗可以先看这几款奶茶" in body
    assert "厚乳" in calls
    assert '"type": "done"' in body


def test_render_fast_menu_reply_is_conversational():
    body = agent_api._render_fast_menu_reply(
        brand="喜茶",
        query="奶茶",
        max_price=None,
        message="给我推荐一杯喜茶的热奶茶",
        results=[
            {
                "id": "m-1",
                "brand": "喜茶",
                "name": "嫣红牛乳茶",
                "price": 15,
                "description": "喜茶经典嫣红牛乳茶，嫣红茶汤与源牧3.8牛乳碰撞，茶香馥郁，口感顺滑，适合热饮。",
                "score": 0.9,
            }
        ],
    )

    assert "喜茶可以先看这几款奶茶：" in body
    assert "1. 嫣红牛乳茶（¥15）" in body
    assert "- 喜茶经典嫣红牛乳茶，嫣红茶汤与源牧3.8牛乳碰撞，茶香馥郁，口感顺滑，适合热饮。" in body


def test_agent_chat_menu_query_keeps_sorting_intent():
    candidates = agent_api._build_menu_query_candidates("给我推荐古茗的经典奶茶top3", "古茗", "奶茶")

    assert "经典奶茶top3" in candidates[0]
    assert "奶茶" in candidates
    assert "牛乳茶" in candidates


def test_extract_brand_supports_explicit_unknown_brand_pattern():
    assert agent_api._extract_brand("今天请给我推荐一杯茉莉奶白的奶茶") == "茉莉奶白"


def test_agent_chat_menu_fast_path_reports_brand_coverage_gap(monkeypatch):
    async def _fake_search_menu_impl(**kwargs):
        if kwargs.get("brand") is None and kwargs.get("query") == "果茶":
            return {
                "results": [
                    {"id": "m-2", "brand": "喜茶", "name": "多肉葡萄", "price": 19, "description": "经典果茶", "score": 0.6}
                ]
            }
        return {"results": []}

    monkeypatch.setattr(agent_api, "search_menu_impl", _fake_search_menu_impl)
    monkeypatch.setattr(agent_api, "get_menu_brand_coverage_impl", lambda **_kwargs: False)
    monkeypatch.setattr(
        agent_api,
        "_generate_unstructured_menu_reply",
        lambda **_kwargs: asyncio.sleep(0, result="如果你今天想喝霸王茶姬的果茶，可以先选清爽、茶感明显一点的方向。"),
    )
    client = _build_client()

    with client.stream(
        "POST",
        "/bobo/agent/chat",
        json={"message": "给我推荐霸王茶姬的果茶", "thread_id": "menu-2"},
    ) as resp:
        body = "".join(resp.iter_text())

    assert resp.status_code == 200
    assert "霸王茶姬" in body
    assert "果茶" in body


def test_agent_chat_menu_fast_path_skips_online_when_brand_coverage_exists(monkeypatch):
    async def _fake_search_menu_impl(**kwargs):
        return {"results": []}

    monkeypatch.setattr(agent_api, "search_menu_impl", _fake_search_menu_impl)
    monkeypatch.setattr(agent_api, "get_menu_brand_coverage_impl", lambda **_kwargs: True)

    freeform_called = False

    async def _fake_freeform(**_kwargs):
        nonlocal freeform_called
        freeform_called = True
        return "不应调用"

    monkeypatch.setattr(agent_api, "_generate_unstructured_menu_reply", _fake_freeform)
    client = _build_client()

    with client.stream(
        "POST",
        "/bobo/agent/chat",
        json={"message": "给我推荐古茗的奶茶", "thread_id": "menu-coverage-1"},
    ) as resp:
        body = "".join(resp.iter_text())

    assert resp.status_code == 200
    assert "没找到 古茗当前条件下 的奶茶推荐" in body
    assert freeform_called is False


def test_agent_chat_menu_fast_path_uses_freeform_llm_when_brand_not_covered(monkeypatch):
    async def _fake_search_menu_impl(**kwargs):
        return {"results": []}

    monkeypatch.setattr(agent_api, "search_menu_impl", _fake_search_menu_impl)
    monkeypatch.setattr(agent_api, "get_menu_brand_coverage_impl", lambda **_kwargs: False)
    monkeypatch.setattr(
        agent_api,
        "_generate_unstructured_menu_reply",
        lambda **_kwargs: asyncio.sleep(0, result="如果你今天想喝茉莉奶白的奶茶，可以先选奶香顺一点、甜度别太高的款。"),
    )
    client = _build_client()

    with client.stream(
        "POST",
        "/bobo/agent/chat",
        json={"message": "今天请给我推荐一杯茉莉奶白的奶茶", "thread_id": "menu-freeform-1"},
    ) as resp:
        body = "".join(resp.iter_text())

    assert resp.status_code == 200
    assert "茉莉奶白" in body
    assert "奶茶" in body


def test_search_online_brand_menu_runs_requests_concurrently(monkeypatch):
    active = 0
    max_active = 0
    calls: list[tuple[str, str | None]] = []

    class _FakeResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None):
            nonlocal active, max_active
            calls.append((url, (params or {}).get("q")))
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.01)
                if "duckduckgo" in url:
                    query = (params or {}).get("q", "")
                    if "菜单" in query:
                        return _FakeResponse(
                            '<a rel="nofollow" class="result__a" href="https://example.com/a">霸王茶姬 花田乌龙</a>'
                            '<a class="result__snippet" href="/x">经典鲜果茶 18元 热门推荐</a>'
                        )
                    return _FakeResponse("<html></html>")
                return _FakeResponse(
                    "<html><head><title>霸王茶姬官网</title><meta name='description' content='热门鲜果茶与经典产品线'></head><body>花田乌龙 18元</body></html>"
                )
            finally:
                active -= 1

    monkeypatch.setattr(online_menu_search.httpx, "AsyncClient", lambda **kwargs: _FakeClient(**kwargs))

    results = asyncio.run(online_menu_search.search_online_brand_menu("霸王茶姬", "果茶"))

    assert max_active >= 2
    assert any("duckduckgo" in url for url, _ in calls)
    assert any("chagee" in url or "example.com/a" in url for url, _ in calls)
    assert any(item["title"] == "霸王茶姬官网" for item in results)
    assert any("花田乌龙" in item["title"] or "花田乌龙" in item.get("snippet", "") for item in results)


def test_rank_online_menu_candidates_heuristic_path_does_not_need_llm(monkeypatch):
    monkeypatch.setattr(
        online_menu_search,
        "_create_async_llm_client",
        lambda: (_ for _ in ()).throw(AssertionError("LLM should not be called in an event loop")),
    )

    async def _run():
        documents = [
            {
                "title": "霸王茶姬-花田乌龙",
                "snippet": "经典鲜果茶 18元 热门推荐",
                "url": "https://www.chagee.com/product",
                "excerpt": "花田乌龙 18元 经典鲜果茶 热门推荐",
            },
            {
                "title": "霸王茶姬-白雾红尘",
                "snippet": "热门产品 19元",
                "url": "https://www.chagee.com/product",
                "excerpt": "白雾红尘 19元 热门推荐",
            },
        ]
        return online_menu_search.rank_online_menu_candidates(
            brand="霸王茶姬",
            query="果茶",
            user_message="给我推荐霸王茶姬的果茶",
            documents=documents,
        )

    result = asyncio.run(_run())

    assert result
    assert result[0]["name"]
    assert "果茶" in str(result[0]["reason"]) or "经典" in str(result[0]["reason"]) or "热门" in str(result[0]["reason"])


def test_rank_online_menu_candidates_filters_site_titles_and_generic_names(monkeypatch):
    monkeypatch.setattr(
        online_menu_search,
        "_create_async_llm_client",
        lambda: (_ for _ in ()).throw(AssertionError("LLM should not be called in an event loop")),
    )

    async def _run():
        documents = [
            {
                "title": "古茗官方网站",
                "snippet": "奶茶官方网站，查看热门产品与品牌资讯",
                "url": "https://www.gumingnc.com",
                "excerpt": "奶茶官方网站，查看热门产品与品牌资讯",
            },
            {
                "title": "抖音",
                "snippet": "古茗奶茶官方账号",
                "url": "https://www.douyin.com",
                "excerpt": "古茗奶茶官方账号",
            },
        ]
        return online_menu_search.rank_online_menu_candidates(
            brand="古茗",
            query="奶茶",
            user_message="给我推荐古茗的奶茶",
            documents=documents,
        )

    result = asyncio.run(_run())

    assert result == []


def test_rank_online_menu_candidates_async_compacts_prompt(monkeypatch):
    captured = {}

    class _FakeResponseMessage:
        def __init__(self, content: str):
            self.content = content

    class _FakeChoice:
        def __init__(self, content: str):
            self.message = _FakeResponseMessage(content)

    class _FakeChatCompletions:
        async def create(self, **kwargs):
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                choices=[
                    _FakeChoice(
                        '{"candidates":[{"name":"花田乌龙","category":"果茶","price":18,"reason":"网页证据显示为热门鲜果茶","source_url":"https://www.chagee.com/product"}]}'
                    )
                ]
            )

    class _FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_FakeChatCompletions())

    monkeypatch.setattr(online_menu_search, "_create_async_llm_client", lambda: _FakeClient())

    result = asyncio.run(
        online_menu_search.rank_online_menu_candidates_async(
            brand="霸王茶姬",
            query="果茶",
            user_message="给我推荐霸王茶姬的果茶",
            documents=[
                {
                    "title": "霸王茶姬-花田乌龙",
                    "snippet": "经典鲜果茶 18元 热门推荐",
                    "url": "https://www.chagee.com/product",
                    "excerpt": "x" * 2000,
                }
            ],
        )
    )

    assert result[0]["name"] == "花田乌龙"
    prompt = captured["kwargs"]["messages"][0]["content"]
    assert len(prompt) < 1800
    assert "x" * 500 not in prompt


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


def test_llm_node_with_mocked_qwen_response(monkeypatch):
    llm = _FakeLLM()
    runtime = {"llm": llm}
    state = {"messages": [("user", "今天喝了什么推荐")] , "max_steps": 10}
    monkeypatch.setattr(
        nodes,
        "build_prompt_bundle",
        lambda **kwargs: {
            "system_prompt": "system",
            "system_prompt_version": "bobo-agent-system.v1",
            "context_version": "bobo-agent-memory-context.v1",
            "memory_bundle": {"prompts": [], "diagnostics": {}, "context_version": "bobo-agent-memory-context.v1"},
        },
    )

    result = __import__("asyncio").run(llm_node(state, runtime))

    assert "messages" in result
    assert result["messages"][0].content == "这是模型回复"
    assert llm.last_messages[0][0] == "system"


def test_llm_node_degrades_when_llm_times_out(monkeypatch):
    class _SlowLLM:
        async def ainvoke(self, _messages):
            raise TimeoutError("llm timed out")

    monkeypatch.setattr(
        nodes,
        "build_prompt_bundle",
        lambda **kwargs: {
            "system_prompt": "system",
            "system_prompt_version": "bobo-agent-system.v1",
            "context_version": "bobo-agent-memory-context.v1",
            "memory_bundle": {"prompts": [], "diagnostics": {}, "context_version": "bobo-agent-memory-context.v1"},
        },
    )
    get_settings.cache_clear()
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "0.01")

    result = __import__("asyncio").run(llm_node({"messages": [("user", "推荐点喝的")], "max_steps": 10}, {"llm": _SlowLLM()}))

    assert "推理服务" in result["messages"][0].content
    get_settings.cache_clear()


def test_tool_node_classifies_timeout_errors():
    class _SlowTool:
        name = "search_menu"

        async def ainvoke(self, _args):
            raise TimeoutError("tool timed out")

    state = {
        "user_id": "u1",
        "max_steps": 3,
        "messages": [_FakeMessage(tool_calls=[{"name": "search_menu", "args": {"query": "葡萄"}, "id": "tool-1"}])],
    }

    result = __import__("asyncio").run(tool_node(state, {"tool_lookup": {"search_menu": _SlowTool()}}))

    payload = json.loads(result["messages"][0].content)
    assert payload["error_category"] == "timeout"
    assert payload["retryable"] is True


def test_tool_node_rejects_invalid_input():
    class _Tool:
        name = "search_menu"

        async def ainvoke(self, _args):
            return {"results": [], "query": "x"}

    state = {
        "user_id": "u1",
        "max_steps": 3,
        "messages": [_FakeMessage(tool_calls=[{"name": "search_menu", "args": {}, "id": "tool-2"}])],
    }

    result = __import__("asyncio").run(tool_node(state, {"tool_lookup": {"search_menu": _Tool()}}))

    payload = json.loads(result["messages"][0].content)
    assert payload["error_category"] == "input_validation"
    assert payload["retryable"] is False


def test_tool_node_rejects_invalid_output():
    class _Tool:
        name = "search_menu"

        async def ainvoke(self, _args):
            return {"results": [{"id": "m1"}], "query": "葡萄"}

    state = {
        "user_id": "u1",
        "max_steps": 3,
        "messages": [_FakeMessage(tool_calls=[{"name": "search_menu", "args": {"query": "葡萄"}, "id": "tool-3"}])],
    }

    result = __import__("asyncio").run(tool_node(state, {"tool_lookup": {"search_menu": _Tool()}}))

    payload = json.loads(result["messages"][0].content)
    assert payload["error_category"] == "output_validation"
    assert payload["retryable"] is False


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
        async def search(self, query, brand=None, top_k=5, source="agent"):
            captured["query"] = query
            captured["brand"] = brand
            captured["top_k"] = top_k
            captured["source"] = source
            return [{"id": "m1", "brand": "喜茶", "name": "多肉葡萄"}]

    captured = {}
    monkeypatch.setattr("app.tooling.operations.get_menu_search_service", lambda: _FakeService())

    result = __import__("asyncio").run(_search_menu_impl(query="葡萄", brand="喜茶", user_id="real-user"))

    assert result["results"][0]["name"] == "多肉葡萄"
    assert captured == {"query": "葡萄", "brand": "喜茶", "top_k": 5, "source": "agent"}


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

    assert {tool.name for tool in tools} >= {"record_drink", "search_menu", "get_stats", "get_recent_records", "get_day", "get_calendar", "update_menu"}


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
