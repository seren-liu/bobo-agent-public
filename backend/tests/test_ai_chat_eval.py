from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import agent as agent_api
from app.memory import retrieval


def _build_client(user_id: str = "u-eval") -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def add_user_id(request, call_next):
        request.state.user_id = user_id
        request.state.request_id = f"req-{user_id}"
        return await call_next(request)

    app.include_router(agent_api.router)
    return TestClient(app)


def _chat(client: TestClient, message: str, thread_id: str) -> str:
    with client.stream(
        "POST",
        "/bobo/agent/chat",
        json={"message": message, "thread_id": thread_id},
    ) as resp:
        body = "".join(resp.iter_text())
    assert resp.status_code == 200
    return body


def test_ai_chat_eval_core_paths(monkeypatch):
    async def _fake_search_menu_impl(**kwargs):
        brand = kwargs.get("brand")
        query = kwargs.get("query")
        if brand == "古茗" and query in {"奶茶", "牛乳茶", "乳茶", "厚乳", "奶香", "经典奶茶", "招牌奶茶"}:
            return {
                "results": [
                    {
                        "id": "g-1",
                        "brand": "古茗",
                        "name": "经典奶香奶茶",
                        "price": 12,
                        "description": "经典奶香，口感顺滑",
                        "score": 0.84,
                    }
                ]
            }
        return {"results": []}

    monkeypatch.setattr(agent_api, "search_menu_impl", _fake_search_menu_impl)
    monkeypatch.setattr(
        agent_api,
        "_generate_unstructured_menu_reply",
        lambda **_kwargs: __import__("asyncio").sleep(0, result="如果你今天想喝霸王茶姬的果茶，可以先选清爽、茶感明显一点的方向。"),
    )
    monkeypatch.setattr(agent_api, "stream_agent_events", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("LLM path should not run for fast-path cases")))

    client = _build_client()

    local_body = _chat(client, "给我推荐古茗的经典奶茶top3", "eval-menu-local")
    online_body = _chat(client, "给我推荐霸王茶姬的果茶", "eval-menu-online")

    assert "经典奶香奶茶" in local_body
    assert "霸王茶姬" in online_body
    assert "果茶" in online_body


def test_ai_chat_eval_fast_paths_and_memory_budget(monkeypatch):
    monkeypatch.setenv("BOBO_MEMORY_PROMPT_MAX_CHARS", "120")
    monkeypatch.setenv("BOBO_MEMORY_PROMPT_PROFILE_CHARS", "40")
    monkeypatch.setenv("BOBO_MEMORY_PROMPT_THREAD_CHARS", "30")
    monkeypatch.setenv("BOBO_MEMORY_PROMPT_MEMORIES_CHARS", "40")
    monkeypatch.setenv("BOBO_MEMORY_PROMPT_PER_ITEM_CHARS", "18")

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
    monkeypatch.setattr(
        agent_api,
        "get_recent_records_impl",
        lambda **_kwargs: {
            "records": [
                {"brand": "喜茶", "name": "多肉葡萄", "price": 19, "consumed_at": "2026-04-04T12:00:00"}
            ]
        },
    )
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

    monkeypatch.setattr(
        retrieval.repository,
        "get_profile",
        lambda user_id: {
            "drink_preferences": {
                "default_sugar": "少糖",
                "default_ice": "去冰",
                "preferred_brands": ["喜茶", "古茗", "霸王茶姬"],
                "preferred_categories": ["果茶", "奶茶"],
            },
            "interaction_preferences": {"reply_style": "brief"},
            "budget_preferences": {"soft_price_ceiling": 20, "price_sensitive": True},
        },
    )
    monkeypatch.setattr(
        retrieval,
        "load_latest_thread_summary",
        lambda user_id, thread_key: "最近在比较低糖果茶，最近在比较低糖果茶",
    )
    monkeypatch.setattr(
        retrieval,
        "search_relevant_memories",
        lambda user_id, query, scope=None, top_k=None: [
            {"content": "最近预算偏紧，推荐便宜一些"},
            {"content": "最近预算偏紧，推荐便宜一些"},
            {"content": "优先果茶，不要太甜"},
        ],
    )

    client = _build_client(user_id="u-eval-2")

    stats_body = _chat(client, "这周我喝了多少杯", "eval-stats")
    recent_body = _chat(client, "我上次喝了什么", "eval-recent")
    day_body = _chat(client, "今天喝了什么", "eval-day")

    bundle = retrieval.build_agent_prompt_context("u-eval-2", "eval-thread", [("user", "推荐便宜一点")], include_metadata=True)

    assert "这周你一共喝了 4 杯" in stats_body
    assert "你最近一杯是 2026-04-04 的 喜茶 多肉葡萄，¥19。" in recent_body
    assert "今天你喝了 2 杯" in day_body
    assert bundle["diagnostics"]["char_count"] <= 120
    assert bundle["diagnostics"]["truncated"] is True
    assert bundle["rendered_text"].count("最近预算偏紧") == 1
