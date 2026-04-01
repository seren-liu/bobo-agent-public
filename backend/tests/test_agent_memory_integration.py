from __future__ import annotations

from types import SimpleNamespace

from app.agent import nodes
from app.memory.profile import patch_profile
from app.memory import repository
from app.tooling.operations import record_drink_impl


class _FakeLLM:
    def __init__(self) -> None:
        self.last_messages = None

    async def ainvoke(self, messages):
        self.last_messages = messages
        return SimpleNamespace(content="ok", tool_calls=[])


def test_record_drink_uses_memory_defaults():
    patch_profile("u-memory-tool", {"drink_preferences": {"default_sugar": "少糖", "default_ice": "少冰"}})

    result = record_drink_impl(
        brand="喜茶",
        name="多肉葡萄",
        user_id="u-memory-tool",
        source="agent",
    )

    assert result["ok"] is True
    record = result["records"][0]
    assert record["sugar"] == "少糖"
    assert record["ice"] == "少冰"


def test_llm_node_injects_memory_context(monkeypatch):
    patch_profile(
        "u-memory-prompt",
        {
            "drink_preferences": {"default_sugar": "少糖", "default_ice": "少冰"},
            "interaction_preferences": {"reply_style": "brief"},
        },
    )
    repository.create_thread("u-memory-prompt", "user-u-memory-prompt:session-test", "会话")
    repository.save_summary(
        user_id="u-memory-prompt",
        thread_key="user-u-memory-prompt:session-test",
        summary_type="rolling",
        summary_text="用户正在比较低糖果茶",
        open_slots=["是否记录今天这杯"],
        covered_message_count=4,
        token_estimate=20,
    )
    repository.create_memory_item(
        user_id="u-memory-prompt",
        memory_type="constraint",
        scope="recommendation",
        content="最近预算紧",
        normalized_fact=None,
        source_kind="chat_extract",
        source_ref="thread",
    )
    monkeypatch.setattr(nodes, "get_agent_context", lambda: {"thread_id": "user-u-memory-prompt:session-test"})

    llm = _FakeLLM()
    result = __import__("asyncio").run(nodes.llm_node({"messages": [("user", "推荐点便宜的")], "user_id": "u-memory-prompt"}, {"llm": llm}))

    assert result["messages"][0].content == "ok"
    rendered = "\n".join(str(item[1]) for item in llm.last_messages if isinstance(item, tuple) and item[0] == "system")
    assert "用户长期画像" in rendered
    assert "回答风格：brief" in rendered
    assert "当前会话摘要" in rendered
    assert "相关长期记忆" in rendered
    assert "reply_style" not in rendered
