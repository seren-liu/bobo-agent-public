from __future__ import annotations

import os

os.environ["JWT_SECRET"] = "test-secret"

from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token
from app.memory import extraction, repository


client = TestClient(app)


def _auth_header(user_id: str = "u-memory") -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_thread_profile_and_memory_flow():
    headers = _auth_header()

    create_resp = client.post("/bobo/agent/threads", json={"title": "测试会话"}, headers=headers)
    assert create_resp.status_code == 200
    thread = create_resp.json()
    thread_key = thread["thread_key"]

    repository.append_message(user_id="u-memory", thread_key=thread_key, role="user", content="以后默认少糖少冰", source="agent")
    repository.append_message(user_id="u-memory", thread_key=thread_key, role="assistant", content="记住了", source="agent")

    list_resp = client.get("/bobo/agent/threads", headers=headers)
    assert list_resp.status_code == 200
    assert any(item["thread_key"] == thread_key for item in list_resp.json())

    profile_resp = client.patch(
        "/bobo/agent/profile",
        json={"drink_preferences": {"default_sugar": "少糖", "default_ice": "少冰"}},
        headers=headers,
    )
    assert profile_resp.status_code == 200
    assert profile_resp.json()["drink_preferences"]["default_sugar"] == "少糖"

    memory = repository.create_memory_item(
        user_id="u-memory",
        memory_type="constraint",
        scope="recommendation",
        content="最近预算紧，推荐便宜一点",
        normalized_fact=None,
        source_kind="chat_extract",
        source_ref=thread_key,
    )
    memories_resp = client.get("/bobo/agent/memories", headers=headers)
    assert memories_resp.status_code == 200
    assert any(item["id"] == memory["id"] for item in memories_resp.json())

    disable_resp = client.post(f"/bobo/agent/memories/{memory['id']}/disable", headers=headers)
    assert disable_resp.status_code == 200

    delete_resp = client.delete(f"/bobo/agent/memories/{memory['id']}", headers=headers)
    assert delete_resp.status_code == 200

    messages_resp = client.get(f"/bobo/agent/threads/{thread_key}/messages", headers=headers)
    assert messages_resp.status_code == 200
    assert len(messages_resp.json()) >= 2


def test_internal_extract_preview_and_reconcile(monkeypatch):
    headers = _auth_header("u-memory-preview")

    create_resp = client.post("/bobo/agent/threads", json={"title": "记忆预览"}, headers=headers)
    assert create_resp.status_code == 200
    thread_key = create_resp.json()["thread_key"]

    repository.append_message(
        user_id="u-memory-preview",
        thread_key=thread_key,
        role="user",
        content="以后默认少糖少冰，最近预算紧一点，同时回答简短就行",
        source="agent",
    )

    monkeypatch.setattr(
        extraction,
        "extract_structured_facts",
        lambda messages, *, thread_key, rule_facts=None: [
            {
                "fact_type": "drink_preference",
                "route": "profile",
                "field_path": "drink_preferences.preferred_categories",
                "value": ["果茶"],
                "scope": "profile",
                "confidence": 0.93,
            },
            {
                "fact_type": "drink_preference",
                "route": "profile",
                "field_path": "drink_preferences.preferred_brands",
                "value": ["喜茶"],
                "scope": "profile",
                "confidence": 0.9,
            },
            {
                "fact_type": "budget_preference",
                "route": "profile",
                "field_path": "budget_preferences.soft_price_ceiling",
                "value": 20,
                "scope": "profile",
                "confidence": 0.88,
            },
        ],
    )

    preview_resp = client.post(
        "/bobo/agent/internal/memories/extract-preview",
        json={"thread_key": thread_key},
        headers=headers,
    )
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    assert preview["thread_key"] == thread_key
    assert preview["result"]["profile_updates"]["interaction_preferences"]["reply_style"] == "brief"
    assert preview["result"]["profile_updates"]["drink_preferences"]["preferred_categories"] == ["果茶"]
    assert preview["result"]["profile_updates"]["drink_preferences"]["preferred_brands"] == ["喜茶"]
    assert preview["result"]["profile_updates"]["budget_preferences"]["soft_price_ceiling"] == 20
    assert preview["result"]["memory_upserts"][0]["normalized_fact"]["kind"] == "budget_constraint"

    extract_resp = client.post("/bobo/agent/internal/memories/extract", headers=headers)
    assert extract_resp.status_code == 200
    extract_payload = extract_resp.json()
    assert extract_payload

    reconcile_resp = client.post("/bobo/agent/internal/profile/reconcile", headers=headers)
    assert reconcile_resp.status_code == 200
    payload = reconcile_resp.json()
    assert payload["thread_count"] >= 1
    assert payload["memory_upsert_count"] >= 1


def test_internal_memory_extract_job_persists_profile_and_memory(monkeypatch):
    headers = _auth_header("u-memory-job")

    create_resp = client.post("/bobo/agent/threads", json={"title": "记忆任务"}, headers=headers)
    assert create_resp.status_code == 200
    thread_key = create_resp.json()["thread_key"]

    repository.append_message(
        user_id="u-memory-job",
        thread_key=thread_key,
        role="user",
        content="以后默认少糖少冰，最近预算紧一点，推荐便宜些，回答简短就行，我喜欢果茶，也常喝喜茶，预算通常 20 元以内",
        source="agent",
    )
    monkeypatch.setattr(
        extraction,
        "extract_structured_facts",
        lambda messages, *, thread_key, rule_facts=None: [
            {
                "fact_type": "drink_preference",
                "route": "profile",
                "field_path": "drink_preferences.preferred_categories",
                "value": ["果茶"],
                "scope": "profile",
                "confidence": 0.93,
            },
            {
                "fact_type": "drink_preference",
                "route": "profile",
                "field_path": "drink_preferences.preferred_brands",
                "value": ["喜茶"],
                "scope": "profile",
                "confidence": 0.9,
            },
            {
                "fact_type": "budget_preference",
                "route": "profile",
                "field_path": "budget_preferences.soft_price_ceiling",
                "value": 20,
                "scope": "profile",
                "confidence": 0.88,
            },
        ],
    )

    class _FakeVectors:
        def upsert_memory_item(self, item):
            return None

    monkeypatch.setattr(extraction, "MemoryVectorService", lambda: _FakeVectors())

    resp = client.post("/bobo/agent/internal/memories/extract", headers=headers)
    assert resp.status_code == 200
    jobs = resp.json()
    assert jobs

    profile = repository.get_profile("u-memory-job")
    assert profile["drink_preferences"]["preferred_categories"] == ["果茶"]
    assert profile["drink_preferences"]["preferred_brands"] == ["喜茶"]
    assert profile["budget_preferences"]["soft_price_ceiling"] == 20
    assert profile["interaction_preferences"]["reply_style"] == "brief"

    memories = repository.list_memories("u-memory-job")
    assert len(memories) == 1
    assert memories[0]["normalized_fact"]["kind"] == "budget_constraint"
    assert memories[0]["normalized_fact"]["preference"] == "lower_price"
