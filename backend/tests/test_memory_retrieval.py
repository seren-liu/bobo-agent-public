from __future__ import annotations

from app.memory import retrieval


def test_load_profile_summary_orders_interaction_budget_drink(monkeypatch):
    monkeypatch.setattr(
        retrieval.repository,
        "get_profile",
        lambda user_id: {
            "drink_preferences": {
                "default_sugar": "少糖",
                "default_ice": "去冰",
                "preferred_brands": ["喜茶", "奈雪"],
                "preferred_categories": ["果茶"],
            },
            "interaction_preferences": {"reply_style": "brief"},
            "budget_preferences": {"soft_price_ceiling": 20, "price_sensitive": True},
        },
    )

    summary = retrieval.load_profile_summary("u-1")
    assert summary.splitlines() == [
        "回答风格：brief",
        "预算偏好：20 元以内优先 / 价格敏感",
        "默认糖冰：少糖 / 去冰",
        "偏好品牌：喜茶, 奈雪",
        "偏好品类：果茶",
    ]


def test_build_agent_prompt_context_prioritizes_profile_blocks(monkeypatch):
    monkeypatch.setattr(
        retrieval.repository,
        "get_profile",
        lambda user_id: {
            "drink_preferences": {"default_sugar": "少糖"},
            "interaction_preferences": {"reply_style": "brief"},
            "budget_preferences": {"soft_price_ceiling": 20},
        },
    )
    monkeypatch.setattr(
        retrieval,
        "load_latest_thread_summary",
        lambda user_id, thread_key: "最近在比较低糖果茶",
    )
    monkeypatch.setattr(
        retrieval,
        "search_relevant_memories",
        lambda user_id, query, scope=None, top_k=None: [{"content": "最近预算偏紧"}],
    )

    prompts = retrieval.build_agent_prompt_context(
        "u-1",
        "thread-1",
        [("user", "推荐便宜一点")],
    )

    assert prompts == [
        ("system", "用户长期画像（优先）：\n回答风格：brief"),
        ("system", "用户长期画像（优先）：\n预算偏好：20 元以内优先"),
        ("system", "用户长期画像（优先）：\n默认糖冰：少糖 / 未设定"),
        ("system", "当前会话摘要：\n最近在比较低糖果茶"),
        ("system", "相关长期记忆：\n- 最近预算偏紧"),
    ]


def test_build_agent_prompt_context_keeps_reply_style_out_of_memory_items(monkeypatch):
    monkeypatch.setattr(
        retrieval.repository,
        "get_profile",
        lambda user_id: {
            "drink_preferences": {"default_sugar": "少糖"},
            "interaction_preferences": {"reply_style": "brief"},
            "budget_preferences": {"soft_price_ceiling": 20},
        },
    )
    monkeypatch.setattr(retrieval, "load_latest_thread_summary", lambda user_id, thread_key: "")
    monkeypatch.setattr(
        retrieval,
        "search_relevant_memories",
        lambda user_id, query, scope=None, top_k=None: [{"content": "最近预算偏紧，推荐便宜一些"}],
    )

    prompts = retrieval.build_agent_prompt_context("u-1", "thread-1", [("user", "推荐便宜一点")])
    rendered = "\n".join(item[1] for item in prompts)

    assert "回答风格：brief" in rendered
    assert "相关长期记忆" in rendered
    assert "reply_style" not in rendered
    assert "最近预算偏紧，推荐便宜一些" in rendered


def test_search_relevant_memories_prefers_vector_hits(monkeypatch):
    memories = [
        {
            "id": "m1",
            "content": "最近预算有点紧，推荐便宜一点",
            "scope": "recommendation",
            "status": "active",
            "salience": 0.6,
            "confidence": 0.7,
            "updated_at": None,
            "created_at": None,
            "expires_at": None,
        },
        {
            "id": "m2",
            "content": "偏好果茶",
            "scope": "recommendation",
            "status": "active",
            "salience": 0.9,
            "confidence": 0.9,
            "updated_at": None,
            "created_at": None,
            "expires_at": None,
        },
    ]

    monkeypatch.setattr(retrieval.repository, "list_memories", lambda user_id: memories)
    monkeypatch.setattr(retrieval.repository, "touch_memory_item", lambda user_id, memory_id: None)

    class _FakeVectors:
        def search_memory_items(self, **kwargs):
            return [{"id": "m1", "score": 0.92}]

    monkeypatch.setattr(retrieval, "MemoryVectorService", lambda: _FakeVectors())

    results = retrieval.search_relevant_memories("u-1", query="预算 便宜", scope="recommendation", top_k=2)

    assert [item["id"] for item in results] == ["m1", "m2"]


def test_search_relevant_memories_falls_back_when_vector_empty(monkeypatch):
    memories = [
        {
            "id": "m1",
            "content": "偏好果茶",
            "scope": "recommendation",
            "status": "active",
            "salience": 0.9,
            "confidence": 0.9,
            "updated_at": None,
            "created_at": None,
            "expires_at": None,
        }
    ]

    monkeypatch.setattr(retrieval.repository, "list_memories", lambda user_id: memories)
    monkeypatch.setattr(retrieval.repository, "touch_memory_item", lambda user_id, memory_id: None)

    class _FakeVectors:
        def search_memory_items(self, **kwargs):
            return []

    monkeypatch.setattr(retrieval, "MemoryVectorService", lambda: _FakeVectors())

    results = retrieval.search_relevant_memories("u-1", query="果茶", scope="recommendation", top_k=1)

    assert [item["id"] for item in results] == ["m1"]


def test_build_agent_prompt_context_applies_budget_and_deduplicates(monkeypatch):
    monkeypatch.setenv("BOBO_MEMORY_PROMPT_MAX_CHARS", "120")
    monkeypatch.setenv("BOBO_MEMORY_PROMPT_PROFILE_CHARS", "40")
    monkeypatch.setenv("BOBO_MEMORY_PROMPT_THREAD_CHARS", "30")
    monkeypatch.setenv("BOBO_MEMORY_PROMPT_MEMORIES_CHARS", "40")
    monkeypatch.setenv("BOBO_MEMORY_PROMPT_PER_ITEM_CHARS", "18")

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

    bundle = retrieval.build_agent_prompt_context("u-1", "thread-1", [("user", "推荐便宜一点")], include_metadata=True)

    assert bundle["diagnostics"]["char_count"] <= 120
    assert bundle["diagnostics"]["truncated"] is True
    rendered = bundle["rendered_text"]
    assert rendered.count("最近预算偏紧") == 1
    assert "回答风格：brief" in rendered
    assert "当前会话摘要" in rendered


def test_build_agent_prompt_context_returns_context_version(monkeypatch):
    monkeypatch.setattr(
        retrieval.repository,
        "get_profile",
        lambda user_id: {
            "drink_preferences": {"default_sugar": "少糖"},
            "interaction_preferences": {"reply_style": "brief"},
            "budget_preferences": {},
        },
    )
    monkeypatch.setattr(retrieval, "load_latest_thread_summary", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(retrieval, "search_relevant_memories", lambda *_args, **_kwargs: [])

    bundle = retrieval.build_agent_prompt_context("u-1", "thread-1", [("user", "推荐便宜一点")], include_metadata=True)

    assert bundle["context_version"] == "bobo-agent-memory-context.v1"
