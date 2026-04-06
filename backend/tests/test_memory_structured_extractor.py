from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.memory_structured_extractor import MemoryStructuredExtractorService


def test_structured_extractor_heuristic_extracts_brand_and_category_preferences():
    service = MemoryStructuredExtractorService(api_key="")

    facts = service.extract_facts(
        [{"role": "user", "content": "我比较喜欢果茶，喜茶也不错，这阵子先别推荐霸王茶姬"}],
        rule_facts=[],
    )

    assert any(
        fact["route"] == "profile"
        and fact["field_path"] == "drink_preferences.preferred_categories"
        and fact["value"] == ["fruit_tea"]
        for fact in facts
    )
    assert any(
        fact["route"] == "profile"
        and fact["field_path"] == "drink_preferences.preferred_brands"
        and fact["value"] == ["喜茶"]
        for fact in facts
    )
    assert any(
        fact["route"] == "memory"
        and fact["normalized_fact"]["kind"] == "brand_constraint"
        and fact["normalized_fact"]["value"] == "霸王茶姬"
        for fact in facts
    )
    assert not any(
        fact["route"] == "memory"
        and fact["normalized_fact"]["kind"] == "brand_constraint"
        and fact["normalized_fact"]["value"] == "喜茶"
        for fact in facts
    )


def test_structured_extractor_falls_back_to_heuristics_when_llm_fails(monkeypatch):
    service = MemoryStructuredExtractorService(api_key="dummy-key")

    monkeypatch.setattr(service, "_extract_via_llm", lambda messages, rule_facts=None: (_ for _ in ()).throw(RuntimeError("boom")))

    facts = service.extract_facts(
        [{"role": "user", "content": "这阵子先别推荐霸王茶姬"}],
        rule_facts=[],
    )

    assert len(facts) == 1
    assert facts[0]["route"] == "memory"
    assert facts[0]["normalized_fact"]["kind"] == "brand_constraint"
    assert facts[0]["normalized_fact"]["value"] == "霸王茶姬"


def test_structured_extractor_normalizes_brand_aliases():
    service = MemoryStructuredExtractorService(api_key="")

    facts = service.extract_facts(
        [{"role": "user", "content": "我常喝一点点，这阵子先别推荐一点点"}],
        rule_facts=[],
    )

    assert any(
        fact["route"] == "profile"
        and fact["field_path"] == "drink_preferences.preferred_brands"
        and fact["value"] == ["1点点"]
        for fact in facts
    )
    assert any(
        fact["route"] == "memory"
        and fact["normalized_fact"]["kind"] == "brand_constraint"
        and fact["normalized_fact"]["value"] == "1点点"
        for fact in facts
    )


def test_structured_extractor_llm_calls_disable_thinking():
    captured: dict[str, object] = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            payload = {"facts": []}
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))]
            )

    class _FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    service = MemoryStructuredExtractorService(api_key="test-key")
    service._create_client = lambda: _FakeClient()

    facts = service._extract_via_llm([{"role": "user", "content": "回答简短就行"}], rule_facts=[])

    assert facts == []
    assert captured["extra_body"] == {"enable_thinking": False}


def test_structured_extractor_normalizes_loose_llm_facts(monkeypatch):
    service = MemoryStructuredExtractorService(api_key="test-key")
    monkeypatch.setattr(
        service,
        "_extract_via_llm",
        lambda messages, rule_facts=None: [
            {
                "route": "profile",
                "field_path": "drink_preferences.preferred_categories",
                "value": "果茶",
                "normalized_fact": "preferred_category: 果茶",
                "content": "我比较喜欢果茶",
            },
            {
                "route": "memory",
                "memory_type": "budget_constraint",
                "memory_scope": "current_session",
                "normalized_fact": "soft_price_ceiling: 20元",
                "content": "最近预算尽量20元以内",
            },
        ],
    )

    facts = service.extract_facts([{"role": "user", "content": "我比较喜欢果茶，最近预算尽量20元以内"}], rule_facts=[])

    assert facts[0]["value"] == ["fruit_tea"]
    assert facts[0]["normalized_fact"] == {
        "kind": "drink_preference",
        "field": "preferred_categories",
        "value": ["fruit_tea"],
    }
    assert facts[1]["memory_type"] == "constraint"
    assert facts[1]["memory_scope"] == "recommendation"
    assert facts[1]["normalized_fact"]["kind"] == "budget_constraint"
    assert facts[1]["normalized_fact"]["soft_price_ceiling"] == 20


def test_structured_extractor_normalizes_reply_style_to_brief(monkeypatch):
    service = MemoryStructuredExtractorService(api_key="test-key")
    monkeypatch.setattr(
        service,
        "_extract_via_llm",
        lambda messages, rule_facts=None: [
            {
                "route": "profile",
                "field_path": "interaction_preferences.reply_style",
                "value": "简短",
                "content": "回答简短就行",
            }
        ],
    )

    facts = service.extract_facts([{"role": "user", "content": "回答简短就行"}], rule_facts=[])

    assert facts == [
        {
            "fact_type": "interaction_preference",
            "route": "profile",
            "field_path": "interaction_preferences.reply_style",
            "value": "brief",
            "memory_type": None,
            "memory_scope": None,
            "normalized_fact": {"kind": "interaction_preference", "field": "reply_style", "value": "brief"},
            "content": "回答简短就行",
            "confidence": 0.7,
            "ttl_days": None,
        }
    ]
