from __future__ import annotations

from app.memory import extraction


def test_persist_extraction_result_routes_multiple_facts(monkeypatch):
    messages = [
        {
            "id": "msg-1",
            "role": "user",
            "content": "以后默认少糖少冰，最近预算紧一点，推荐便宜些，回答简短就行，我喜欢果茶，也常喝喜茶，预算通常 20 元以内",
        }
    ]
    patch_calls: list[dict[str, object]] = []
    created_calls: list[dict[str, object]] = []
    vector_upserts: list[dict[str, object]] = []
    structured_calls: list[tuple[list[dict[str, object]], str, list[dict[str, object]] | None]] = []

    monkeypatch.setattr(extraction.repository, "list_recent_user_messages", lambda user_id, thread_key, limit=10: messages[::-1])
    monkeypatch.setattr(
        extraction,
        "extract_structured_facts",
        lambda messages, *, thread_key, rule_facts=None: structured_calls.append((messages, thread_key, rule_facts))
        or [
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
                "confidence": 0.91,
            },
            {
                "fact_type": "budget_preference",
                "route": "profile",
                "field_path": "budget_preferences.soft_price_ceiling",
                "value": 20,
                "scope": "profile",
                "confidence": 0.89,
            },
        ],
    )

    def fake_apply_profile_updates(user_id: str, patch: dict[str, object]) -> dict[str, object]:
        patch_calls.append(patch)
        return {"user_id": user_id, **patch}

    def fake_upsert_memory_item_by_fact(**kwargs):
        created_calls.append(kwargs)
        item = dict(kwargs)
        item["id"] = "memory-1"
        item["created_at"] = "created"
        item["updated_at"] = "updated"
        item["last_used_at"] = None
        return item

    class _FakeVectors:
        def upsert_memory_item(self, item):
            vector_upserts.append(item)

    monkeypatch.setattr(extraction, "apply_profile_updates", fake_apply_profile_updates)
    monkeypatch.setattr(extraction.repository, "upsert_memory_item_by_fact", fake_upsert_memory_item_by_fact)
    monkeypatch.setattr(extraction, "MemoryVectorService", lambda: _FakeVectors())

    result = extraction.persist_extraction_result("u-1", "thread-1")

    assert len(patch_calls) == 1
    patch = patch_calls[0]
    assert patch["drink_preferences"]["default_sugar"] == "少糖"
    assert patch["drink_preferences"]["default_ice"] == "少冰"
    assert patch["interaction_preferences"]["reply_style"] == "brief"
    assert patch["drink_preferences"]["preferred_categories"] == ["果茶"]
    assert patch["drink_preferences"]["preferred_brands"] == ["喜茶"]
    assert patch["budget_preferences"]["soft_price_ceiling"] == 20
    assert len(created_calls) == 1
    assert created_calls[0]["memory_type"] == "constraint"
    assert created_calls[0]["scope"] == "recommendation"
    assert created_calls[0]["normalized_fact"]["kind"] == "budget_constraint"
    assert created_calls[0]["normalized_fact"]["preference"] == "lower_price"
    assert created_calls[0]["content"].startswith("最近预算偏紧")
    assert len(result["created_memory_items"]) == 1
    assert result["created_memory_items"][0]["id"] == "memory-1"
    assert vector_upserts and vector_upserts[0]["id"] == "memory-1"
    assert len(structured_calls) == 1
    assert structured_calls[0][1] == "thread-1"
    assert result["diagnostics"]["structured_fact_count"] == 3
    assert result["diagnostics"]["memory_upsert_count"] == 1
    assert all(item.get("normalized_fact", {}).get("kind") != "interaction_preference" for item in created_calls)


def test_structured_extraction_failure_falls_back_to_rules(monkeypatch):
    messages = [
        {
            "id": "msg-1",
            "role": "user",
            "content": "最近预算紧一点，同时回答简短就行",
        }
    ]
    patch_calls: list[dict[str, object]] = []
    upsert_calls: list[dict[str, object]] = []
    structured_calls: list[dict[str, object]] = []

    monkeypatch.setattr(extraction.repository, "list_recent_user_messages", lambda user_id, thread_key, limit=10: messages[::-1])
    monkeypatch.setattr(
        extraction,
        "extract_structured_facts",
        lambda messages, *, thread_key, rule_facts=None: structured_calls.append(
            {"messages": messages, "thread_key": thread_key, "rule_facts": rule_facts}
        )
        or (_ for _ in ()).throw(RuntimeError("structured extractor boom")),
    )

    def fake_apply_profile_updates(user_id: str, patch: dict[str, object]) -> dict[str, object]:
        patch_calls.append(patch)
        return {"user_id": user_id, **patch}

    def fake_upsert_memory_item_by_fact(**kwargs):
        upsert_calls.append(kwargs)
        item = dict(kwargs)
        item["id"] = "memory-existing"
        item["created_at"] = "created"
        item["updated_at"] = "updated"
        item["last_used_at"] = None
        return item

    class _FakeVectors:
        def upsert_memory_item(self, item):
            return None

    monkeypatch.setattr(extraction, "apply_profile_updates", fake_apply_profile_updates)
    monkeypatch.setattr(extraction.repository, "upsert_memory_item_by_fact", fake_upsert_memory_item_by_fact)
    monkeypatch.setattr(extraction, "MemoryVectorService", lambda: _FakeVectors())

    result = extraction.persist_extraction_result("u-1", "thread-1")

    assert len(patch_calls) == 1
    assert patch_calls[0]["interaction_preferences"]["reply_style"] == "brief"
    assert len(result["created_memory_items"]) == 1
    assert result["created_memory_items"][0]["id"] == "memory-existing"
    assert len(upsert_calls) == 1
    assert result["diagnostics"]["structured_error_count"] == 1
    assert result["diagnostics"]["structured_fact_count"] == 0
    assert len(structured_calls) == 1


def test_structured_extraction_hook_is_invoked_for_mixed_intent(monkeypatch):
    messages = [
        {
            "id": "msg-1",
            "role": "user",
            "content": "最近预算紧一点，同时回答简短就行",
        }
    ]
    structured_calls: list[tuple[list[dict[str, object]], str, list[dict[str, object]] | None]] = []

    monkeypatch.setattr(extraction.repository, "list_recent_user_messages", lambda user_id, thread_key, limit=10: messages[::-1])
    monkeypatch.setattr(extraction, "apply_profile_updates", lambda user_id, patch: {"user_id": user_id, **patch})
    monkeypatch.setattr(
        extraction,
        "extract_structured_facts",
        lambda messages, *, thread_key, rule_facts=None: structured_calls.append((messages, thread_key, rule_facts)) or [],
    )

    class _FakeVectors:
        def upsert_memory_item(self, item):
            return None

    monkeypatch.setattr(extraction, "MemoryVectorService", lambda: _FakeVectors())
    monkeypatch.setattr(extraction.repository, "upsert_memory_item_by_fact", lambda **kwargs: {"id": "memory-hook", **kwargs})

    extraction.persist_candidate_memories("u-1", "thread-1")

    assert len(structured_calls) == 1
    assert structured_calls[0][1] == "thread-1"


def test_build_extraction_result_uses_structured_extractor_for_brand_and_category(monkeypatch):
    messages = [
        {
            "id": "msg-2",
            "role": "user",
            "content": "我比较喜欢果茶，喜茶也不错",
        }
    ]

    monkeypatch.setattr(extraction.repository, "list_recent_user_messages", lambda user_id, thread_key, limit=10: messages[::-1])

    result = extraction.build_extraction_result("u-structured", "thread-structured")

    assert result["diagnostics"]["structured_fact_count"] >= 2
    assert result["profile_updates"]["drink_preferences"]["preferred_categories"] == ["fruit_tea"]
    assert result["profile_updates"]["drink_preferences"]["preferred_brands"] == ["喜茶"]


def test_build_extraction_result_falls_back_when_structured_extractor_errors(monkeypatch):
    messages = [
        {
            "id": "msg-3",
            "role": "user",
            "content": "最近预算紧一点，同时回答简短就行",
        }
    ]

    monkeypatch.setattr(extraction.repository, "list_recent_user_messages", lambda user_id, thread_key, limit=10: messages[::-1])
    monkeypatch.setattr(extraction, "extract_structured_facts", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    result = extraction.build_extraction_result("u-structured-error", "thread-structured-error")

    assert result["diagnostics"]["structured_error_count"] == 1
    assert result["profile_updates"]["interaction_preferences"]["reply_style"] == "brief"
    assert result["memory_upserts"][0]["normalized_fact"]["kind"] == "budget_constraint"


def test_build_extraction_result_routes_temporary_category_preference_to_memory(monkeypatch):
    messages = [
        {
            "id": "msg-temp-category",
            "role": "user",
            "content": "这段时间我想喝果茶类",
        }
    ]

    monkeypatch.setattr(extraction.repository, "list_recent_user_messages", lambda user_id, thread_key, limit=10: messages[::-1])

    result = extraction.build_extraction_result("u-temp-category", "thread-temp-category")

    assert result["profile_updates"] == {}
    assert len(result["memory_upserts"]) == 1
    assert result["memory_upserts"][0]["memory_type"] == "preference"
    assert result["memory_upserts"][0]["scope"] == "recommendation"
    assert result["memory_upserts"][0]["normalized_fact"]["kind"] == "drink_preference"
    assert result["memory_upserts"][0]["normalized_fact"]["value"] == ["fruit_tea"]
