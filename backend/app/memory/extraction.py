from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import get_settings
from app.memory.profile import apply_profile_updates
from app.memory import repository
from app.services.memory_structured_extractor import MemoryStructuredExtractorService
from app.services.memory_vectors import MemoryVectorService

_REPLY_STYLE_KEYWORDS = (
    "简短",
    "简洁",
    "简单点",
    "简单些",
    "少点废话",
    "直接点",
    "别太长",
    "别啰嗦",
    "精简",
)
_DEFAULT_SUGAR_KEYWORDS = ("少糖", "无糖", "半糖")
_DEFAULT_ICE_KEYWORDS = ("少冰", "去冰", "常温", "热的")
_TEMPORARY_BUDGET_HINTS = (
    "最近",
    "这阵子",
    "这段时间",
    "目前",
    "暂时",
    "先别",
    "先不要",
    "近期",
)
_STAGE_BUDGET_HINTS = (
    "预算紧",
    "预算有点紧",
    "预算比较紧",
    "尽量便宜",
    "便宜些",
    "便宜一点",
    "别太贵",
    "推荐便宜",
    "控制一下预算",
)
_PROFILE_BUDGET_HINTS = (
    "通常",
    "一般",
    "平时",
    "以后",
    "默认",
    "我常常",
    "我一般",
    "我通常",
)
_PRICE_PATTERN = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*元")
_STRUCTURED_INTENT_HINTS = (
    "喜欢",
    "偏好",
    "爱喝",
    "别推荐",
    "别推",
    "不要",
    "喝腻了",
    "一般喝",
    "通常喝",
    "以内",
    "不要超过",
    "别超过",
)


def _ttl(days: int | None = None) -> datetime:
    days = max(int(days or get_settings().memory_item_default_ttl_days or 90), 1)
    return datetime.now(UTC) + timedelta(days=days)


def _message_text(message: dict[str, Any]) -> str:
    return str(message.get("content") or "").strip()


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _as_price_value(text: str) -> float | int | None:
    match = _PRICE_PATTERN.search(text)
    if not match:
        return None
    raw = float(match.group("value"))
    return int(raw) if raw.is_integer() else round(raw, 1)


def _build_fact(
    *,
    fact_type: str,
    route: str,
    field_path: str | None = None,
    value: Any = None,
    scope: str | None = None,
    content: str,
    normalized_fact: dict[str, Any] | None = None,
    confidence: float = 0.8,
    ttl_days: int | None = None,
    memory_type: str | None = None,
    memory_scope: str | None = None,
    source_message: dict[str, Any] | None = None,
    source_ref: str | None = None,
) -> dict[str, Any]:
    fact: dict[str, Any] = {
        "fact_type": fact_type,
        "route": route,
        "content": content,
        "confidence": confidence,
    }
    if field_path is not None:
        fact["field_path"] = field_path
    if value is not None:
        fact["value"] = value
    if scope is not None:
        fact["scope"] = scope
    if normalized_fact is not None:
        fact["normalized_fact"] = normalized_fact
    if ttl_days is not None:
        fact["ttl_days"] = ttl_days
    if memory_type is not None:
        fact["memory_type"] = memory_type
    if memory_scope is not None:
        fact["memory_scope"] = memory_scope
    if source_message:
        if source_message.get("id") is not None:
            fact["source_message_id"] = str(source_message["id"])
        if source_message.get("created_at") is not None:
            fact["source_message_created_at"] = source_message["created_at"]
    if source_ref is not None:
        fact["source_ref"] = source_ref
    return fact


def _message_has_multi_intent_hint(content: str) -> bool:
    return _contains_any(content, ("同时", "另外", "以及", "并且", "还有"))


def _needs_structured_intent_parse(content: str) -> bool:
    return _contains_any(content, _STRUCTURED_INTENT_HINTS)


def _extract_reply_style_facts(content: str, message: dict[str, Any], thread_key: str) -> list[dict[str, Any]]:
    if not _contains_any(content, _REPLY_STYLE_KEYWORDS):
        return []
    return [
        _build_fact(
            fact_type="interaction_preference",
            route="profile",
            field_path="interaction_preferences.reply_style",
            value="brief",
            scope="profile",
            content="回答风格偏好：简短",
            normalized_fact={"kind": "interaction_preference", "field": "reply_style", "value": "brief"},
            confidence=0.95,
            source_message=message,
            source_ref=thread_key,
        )
    ]


def _extract_drink_facts(content: str, message: dict[str, Any], thread_key: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    if _contains_any(content, _DEFAULT_SUGAR_KEYWORDS):
        for keyword, value in (("少糖", "少糖"), ("无糖", "无糖"), ("半糖", "半糖")):
            if keyword in content:
                facts.append(
                    _build_fact(
                        fact_type="drink_preference",
                        route="profile",
                        field_path="drink_preferences.default_sugar",
                        value=value,
                        scope="profile",
                        content=f"默认糖度偏好：{value}",
                        normalized_fact={"kind": "drink_preference", "field": "default_sugar", "value": value},
                        confidence=0.94,
                        source_message=message,
                        source_ref=thread_key,
                    )
                )
                break
    if _contains_any(content, _DEFAULT_ICE_KEYWORDS):
        for keyword, value in (("少冰", "少冰"), ("去冰", "去冰"), ("常温", "常温"), ("热的", "热的")):
            if keyword in content:
                facts.append(
                    _build_fact(
                        fact_type="drink_preference",
                        route="profile",
                        field_path="drink_preferences.default_ice",
                        value=value,
                        scope="profile",
                        content=f"默认冰量偏好：{value}",
                        normalized_fact={"kind": "drink_preference", "field": "default_ice", "value": value},
                        confidence=0.94,
                        source_message=message,
                        source_ref=thread_key,
                    )
                )
                break
    return facts


def _extract_budget_facts(content: str, message: dict[str, Any], thread_key: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    has_budget_signal = "预算" in content or "价格" in content or "便宜" in content or "元" in content
    if not has_budget_signal:
        return facts

    ceiling = _as_price_value(content)
    stage_hint = _contains_any(content, _TEMPORARY_BUDGET_HINTS) or _contains_any(content, _STAGE_BUDGET_HINTS)
    stable_hint = _contains_any(content, _PROFILE_BUDGET_HINTS)

    if ceiling is not None and stable_hint and not stage_hint:
        facts.append(
            _build_fact(
                fact_type="budget_preference",
                route="profile",
                field_path="budget_preferences.soft_price_ceiling",
                value=ceiling,
                scope="profile",
                content=f"预算偏好：{ceiling} 元以内",
                normalized_fact={
                    "kind": "budget_preference",
                    "field": "soft_price_ceiling",
                    "value": ceiling,
                },
                confidence=0.9,
                source_message=message,
                source_ref=thread_key,
            )
        )
        if "便宜" in content or "价格敏感" in content:
            facts.append(
                _build_fact(
                    fact_type="budget_preference",
                    route="profile",
                    field_path="budget_preferences.price_sensitive",
                    value=True,
                    scope="profile",
                    content="预算偏好：价格敏感",
                    normalized_fact={
                        "kind": "budget_preference",
                        "field": "price_sensitive",
                        "value": True,
                    },
                    confidence=0.8,
                    source_message=message,
                    source_ref=thread_key,
                )
            )
        return facts

    if stage_hint or "预算" in content or "便宜" in content:
        if ceiling is not None:
            content_bits = [f"最近预算偏紧，尽量控制在{ceiling}元以内"]
        elif "便宜" in content:
            content_bits = ["最近预算偏紧，推荐便宜一些"]
        else:
            content_bits = ["最近预算偏紧"]
        facts.append(
            _build_fact(
                fact_type="budget_constraint",
                route="memory",
                scope="memory",
                memory_type="constraint",
                memory_scope="recommendation",
                content="，".join(content_bits),
                normalized_fact={
                    "kind": "budget_constraint",
                    "constraint_type": "price",
                    "preference": "lower_price",
                    "time_scope": "recent" if stage_hint else "unspecified",
                    "soft_price_ceiling": ceiling,
                },
                confidence=0.87,
                ttl_days=45,
                source_message=message,
                source_ref=thread_key,
            )
        )
    return facts


def extract_rule_based_facts(messages: list[dict[str, Any]], thread_key: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for message in messages:
        content = _message_text(message)
        if not content:
            continue
        facts.extend(_extract_reply_style_facts(content, message, thread_key))
        facts.extend(_extract_drink_facts(content, message, thread_key))
        facts.extend(_extract_budget_facts(content, message, thread_key))
    return facts


def extract_structured_facts(
    messages: list[dict[str, Any]],
    *,
    thread_key: str,
    rule_facts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    source_message = messages[-1] if messages else None
    extracted = MemoryStructuredExtractorService().extract_facts(messages, rule_facts=rule_facts)
    facts: list[dict[str, Any]] = []
    for fact in extracted:
        facts.append(
            _build_fact(
                fact_type=str(fact.get("fact_type") or "drink_preference"),
                route=str(fact.get("route") or "profile"),
                field_path=fact.get("field_path"),
                value=fact.get("value"),
                scope="profile" if str(fact.get("route") or "profile") == "profile" else "memory",
                content=str(fact.get("content") or "")[:240],
                normalized_fact=fact.get("normalized_fact"),
                confidence=float(fact.get("confidence") or 0.7),
                ttl_days=int(fact["ttl_days"]) if fact.get("ttl_days") is not None else None,
                memory_type=fact.get("memory_type"),
                memory_scope=fact.get("memory_scope"),
                source_message=source_message,
                source_ref=thread_key,
            )
        )
    return facts


def _needs_structured_extraction(content: str, message_facts: list[dict[str, Any]]) -> bool:
    if _needs_structured_intent_parse(content):
        return True
    if len(message_facts) >= 3:
        return True
    if len(message_facts) >= 2 and _message_has_multi_intent_hint(content):
        return True
    return False


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze_value(inner)) for key, inner in value.items()))
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _merge_profile_updates(profile_updates: dict[str, Any], fact: dict[str, Any]) -> None:
    field_path = str(fact.get("field_path") or "")
    value = fact.get("value")
    if not field_path:
        return
    section, _, leaf = field_path.partition(".")
    if not section or not leaf:
        return
    target = profile_updates.setdefault(section, {})
    if isinstance(target, dict):
        target[leaf] = value


def _merge_and_resolve_facts(facts: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "canonical_facts": [],
        "profile_updates": {},
        "memory_upserts": [],
        "memory_deactivations": [],
        "diagnostics": {
            "rule_fact_count": 0,
            "structured_fact_count": 0,
            "structured_error_count": 0,
            "profile_fact_count": 0,
            "memory_fact_count": 0,
        },
    }

    seen_facts: set[tuple[Any, ...]] = set()
    for fact in facts:
        route = str(fact.get("route") or "")
        identity = (
            route,
            fact.get("fact_type"),
            fact.get("field_path"),
            _freeze_value(fact.get("value")),
            fact.get("scope"),
            _freeze_value(fact.get("normalized_fact") or {}),
        )
        if identity in seen_facts:
            continue
        seen_facts.add(identity)
        result["canonical_facts"].append(fact)
        if route == "profile":
            _merge_profile_updates(result["profile_updates"], fact)
            result["diagnostics"]["profile_fact_count"] += 1
        elif route == "memory":
            result["memory_upserts"].append(
                {
                    "memory_type": fact.get("memory_type") or "constraint",
                    "scope": fact.get("memory_scope") or fact.get("scope") or "recommendation",
                    "content": str(fact.get("content") or "")[:240],
                    "normalized_fact": fact.get("normalized_fact"),
                    "source_kind": "chat_extract",
                    "source_ref": fact.get("source_ref"),
                    "confidence": float(fact.get("confidence") or 0.5),
                    "salience": max(float(fact.get("confidence") or 0.5), 0.5),
                    "status": "active",
                    "expires_at": _ttl(int(fact["ttl_days"])) if fact.get("ttl_days") else None,
                }
            )
            result["diagnostics"]["memory_fact_count"] += 1
    return result


def build_extraction_result(user_id: str, thread_key: str) -> dict[str, Any]:
    recent_messages = list(reversed(repository.list_recent_user_messages(user_id, thread_key, limit=10)))

    rule_facts: list[dict[str, Any]] = []
    structured_facts: list[dict[str, Any]] = []
    structured_error_count = 0

    for message in recent_messages:
        content = _message_text(message)
        if not content:
            continue
        message_rule_facts = extract_rule_based_facts([message], thread_key)
        rule_facts.extend(message_rule_facts)
        if _needs_structured_extraction(content, message_rule_facts):
            try:
                message_structured_facts = extract_structured_facts([message], thread_key=thread_key, rule_facts=message_rule_facts)
            except Exception:
                structured_error_count += 1
                message_structured_facts = []
            structured_facts.extend(message_structured_facts)

    canonical_facts = rule_facts + structured_facts
    result = _merge_and_resolve_facts(canonical_facts)
    result["diagnostics"]["rule_fact_count"] = len(rule_facts)
    result["diagnostics"]["structured_fact_count"] = len(structured_facts)
    result["diagnostics"]["structured_error_count"] = structured_error_count
    result["diagnostics"]["canonical_fact_count"] = len(result["canonical_facts"])
    result["diagnostics"]["user_message_count"] = len(recent_messages)
    return result


def persist_extraction_result(user_id: str, thread_key: str) -> dict[str, Any]:
    result = build_extraction_result(user_id, thread_key)

    profile_updates = result.get("profile_updates") or {}
    if profile_updates:
        result["profile"] = apply_profile_updates(user_id, profile_updates)

    upserted: list[dict[str, Any]] = []
    if result.get("memory_upserts"):
        vector_service = MemoryVectorService()
        for candidate in result["memory_upserts"]:
            item = repository.upsert_memory_item_by_fact(
                user_id=user_id,
                memory_type=str(candidate.get("memory_type") or "constraint"),
                scope=str(candidate.get("scope") or "recommendation"),
                content=str(candidate.get("content") or "")[:240],
                normalized_fact=candidate.get("normalized_fact"),
                source_kind=str(candidate.get("source_kind") or "chat_extract"),
                source_ref=candidate.get("source_ref"),
                confidence=float(candidate.get("confidence") or 0.5),
                salience=float(candidate.get("salience") or 0.5),
                status=str(candidate.get("status") or "active"),
                expires_at=candidate.get("expires_at"),
            )
            vector_service.upsert_memory_item(item)
            upserted.append(item)
    result["created_memory_items"] = upserted
    result["diagnostics"]["memory_upsert_count"] = len(upserted)
    result["diagnostics"]["profile_update_count"] = len(profile_updates)
    return result


def extract_candidate_memories(user_id: str, thread_key: str) -> list[dict[str, Any]]:
    result = build_extraction_result(user_id, thread_key)
    return list(result.get("memory_upserts") or [])


def persist_candidate_memories(user_id: str, thread_key: str) -> list[dict[str, Any]]:
    result = persist_extraction_result(user_id, thread_key)
    return list(result.get("created_memory_items") or [])
