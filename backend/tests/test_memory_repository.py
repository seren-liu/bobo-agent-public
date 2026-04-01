from __future__ import annotations

from app.memory import repository


def test_list_recent_user_messages_returns_latest_user_messages_first():
    user_id = "u-recent-messages"
    thread_key = "user-u-recent-messages:session-test"

    repository.create_thread(user_id, thread_key, "会话")
    repository.append_message(user_id=user_id, thread_key=thread_key, role="assistant", content="hello", source="agent")
    repository.append_message(user_id=user_id, thread_key=thread_key, role="user", content="第一句", source="agent")
    repository.append_message(user_id=user_id, thread_key=thread_key, role="user", content="第二句", source="agent")
    repository.append_message(user_id=user_id, thread_key=thread_key, role="assistant", content="world", source="agent")

    messages = repository.list_recent_user_messages(user_id, thread_key, limit=2)

    assert [item["content"] for item in messages] == ["第二句", "第一句"]
    assert all(item["role"] == "user" for item in messages)


def test_upsert_memory_item_by_fact_reuses_same_fact_and_keeps_distinct_facts_separate():
    user_id = "u-memory-fact"

    first = repository.upsert_memory_item_by_fact(
        user_id=user_id,
        memory_type="constraint",
        scope="recommendation",
        content="最近预算紧，推荐便宜一点",
        normalized_fact={"kind": "budget_constraint", "preference": "lower_price"},
        source_kind="chat_extract",
        source_ref="thread-a",
        confidence=0.7,
        salience=0.8,
    )
    second = repository.upsert_memory_item_by_fact(
        user_id=user_id,
        memory_type="constraint",
        scope="recommendation",
        content="最近预算紧，推荐更便宜一点",
        normalized_fact={"kind": "budget_constraint", "preference": "lower_price"},
        source_kind="chat_extract",
        source_ref="thread-b",
        confidence=0.9,
        salience=0.6,
    )
    third = repository.upsert_memory_item_by_fact(
        user_id=user_id,
        memory_type="constraint",
        scope="recommendation",
        content="最近预算紧，推荐便宜一点",
        normalized_fact={"kind": "budget_constraint", "preference": "lower_price", "channel": "delivery"},
        source_kind="chat_extract",
        source_ref="thread-c",
    )

    assert first["id"] == second["id"]
    assert second["content"] == "最近预算紧，推荐更便宜一点"
    assert second["source_ref"] == "thread-b"
    assert third["id"] != first["id"]
    assert len(repository.list_memories(user_id, include_inactive=True)) == 2
