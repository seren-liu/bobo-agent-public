from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from app.core.brands import canonicalize_brand_names, known_brand_names
from app.core.config import get_settings
from app.core.resilience import classify_dependency_error, get_circuit_breaker
from app.services.llm_budget import extract_usage_tokens, record_usage

logger = logging.getLogger("bobo.memory.structured")

STRUCTURED_EXTRACTION_PROMPT = (
    "你是 Bobo 奶茶助手的长期记忆结构化抽取器。"
    "请从用户最近的一句或几句话里抽取稳定偏好和阶段性约束，"
    "只返回 JSON，不要解释，不要 markdown。"
    '格式为 {"facts":[...]}。'
    "可输出的 fact_type 仅限：drink_preference、budget_preference、budget_constraint、interaction_preference。"
    "route 仅限 profile 或 memory。"
    "field_path 仅限：drink_preferences.default_sugar、drink_preferences.default_ice、"
    "drink_preferences.preferred_brands、drink_preferences.preferred_categories、"
    "interaction_preferences.reply_style、budget_preferences.soft_price_ceiling、budget_preferences.price_sensitive。"
    "当信息是阶段性/情境性/近期限制时，使用 route=memory，并提供 memory_type、memory_scope、normalized_fact、content、ttl_days。"
    "当信息是稳定默认偏好时，使用 route=profile，并提供 field_path、value、normalized_fact、content。"
    "没有可抽取内容时返回 {\"facts\":[]}。"
)

STRUCTURED_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "fact_type": {"type": "string"},
                    "route": {"type": "string", "enum": ["profile", "memory"]},
                    "field_path": {"type": ["string", "null"]},
                    "value": {},
                    "memory_type": {"type": ["string", "null"]},
                    "memory_scope": {"type": ["string", "null"]},
                    "normalized_fact": {"type": ["object", "null"]},
                    "content": {"type": "string"},
                    "confidence": {"type": ["number", "null"]},
                    "ttl_days": {"type": ["integer", "null"]},
                },
                "required": ["fact_type", "route", "content"],
            },
        }
    },
    "required": ["facts"],
}

STRICT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "memory_structured_extraction",
        "strict": True,
        "schema": STRUCTURED_RESULT_SCHEMA,
    },
}

_CATEGORY_MAP = {
    "果茶": "fruit_tea",
    "奶茶": "milk_tea",
    "轻乳茶": "light_milk_tea",
    "纯茶": "pure_tea",
    "柠檬茶": "lemon_tea",
    "咖啡": "coffee",
}
_CATEGORY_ALIASES = {value: value for value in _CATEGORY_MAP.values()} | {key: value for key, value in _CATEGORY_MAP.items()}
_PREFERENCE_HINTS = ("喜欢", "偏好", "爱喝", "更爱", "常喝", "一般喝", "通常喝", "不错")
_AVOID_HINTS = ("别推荐", "别推", "先别", "不要", "不想喝", "喝腻了", "排斥")
_TRANSIENT_HINTS = ("最近", "这阵子", "近期", "暂时", "先")
_PRICE_PATTERN = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*元")
_CLAUSE_SPLIT_PATTERN = re.compile(r"[，,。；;、\n]+")


class MemoryStructuredExtractorService:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "") or os.getenv("QWEN_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
        self.base_url = base_url or os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.model = model or os.getenv("MEMORY_EXTRACTION_MODEL", "") or os.getenv("QWEN_CHAT_MODEL", "qwen3-32b")
        self.user_id = user_id

    def _create_client(self) -> Any:
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("openai package is required for structured memory extraction") from exc

        if not self.api_key:
            raise RuntimeError("structured extraction API key is required")
        return OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=20)

    @staticmethod
    def _extract_text_content(raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list):
            return "\n".join(
                str(chunk.get("text", ""))
                for chunk in raw
                if isinstance(chunk, dict) and chunk.get("type") == "text"
            )
        return str(raw)

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        content = text.strip()
        if content.startswith("```") and content.endswith("```"):
            lines = content.splitlines()
            if len(lines) >= 2:
                return "\n".join(lines[1:-1]).strip()
        return content

    @staticmethod
    def _normalize_field_value(field_path: str, value: Any) -> Any:
        if field_path == "interaction_preferences.reply_style":
            text = str(value or "").strip().lower()
            if text in {"简短", "简洁", "brief", "short", "精简"}:
                return "brief"
            return value
        if field_path == "drink_preferences.preferred_categories":
            if value is None:
                return []
            if isinstance(value, list):
                values = [str(item).strip() for item in value if str(item).strip()]
            else:
                values = [str(value).strip()]
            normalized: list[str] = []
            for item in values:
                mapped = _CATEGORY_ALIASES.get(item, item)
                if mapped and mapped not in normalized:
                    normalized.append(mapped)
            return normalized
        if field_path in {
            "drink_preferences.preferred_brands",
        }:
            if value is None:
                return []
            if isinstance(value, list):
                return canonicalize_brand_names(str(item) for item in value if str(item).strip())
            return canonicalize_brand_names([str(value)])
        if field_path == "budget_preferences.soft_price_ceiling" and isinstance(value, str):
            try:
                number = float(value)
                return int(number) if number.is_integer() else round(number, 1)
            except ValueError:
                return value
        return value

    @staticmethod
    def _infer_normalized_fact(route: str, field_path: str | None, value: Any, content: str) -> dict[str, Any] | None:
        if route == "profile" and field_path:
            field = field_path.split(".")[-1]
            return {"kind": "drink_preference" if field_path.startswith("drink_preferences.") else "interaction_preference" if field_path.startswith("interaction_preferences.") else "budget_preference", "field": field, "value": value}

        if route == "memory":
            brands = canonicalize_brand_names(brand for brand in known_brand_names() if brand in content)
            price = MemoryStructuredExtractorService._extract_price(content)
            if any(hint in content for hint in _AVOID_HINTS) and brands:
                return {
                    "kind": "brand_constraint",
                    "constraint_type": "brand",
                    "preference": "avoid",
                    "value": brands[0],
                    "time_scope": "recent" if any(hint in content for hint in _TRANSIENT_HINTS) else "unspecified",
                }
            if "预算" in content or "便宜" in content or price is not None:
                return {
                    "kind": "budget_constraint",
                    "constraint_type": "price",
                    "preference": "lower_price",
                    "time_scope": "recent" if any(hint in content for hint in _TRANSIENT_HINTS) else "unspecified",
                    "soft_price_ceiling": price,
                }
        return None

    @staticmethod
    def _infer_fact_type(route: str, field_path: str | None, normalized_fact: dict[str, Any] | None) -> str:
        if isinstance(normalized_fact, dict):
            kind = str(normalized_fact.get("kind") or "")
            if kind == "interaction_preference":
                return "interaction_preference"
            if kind == "budget_preference":
                return "budget_preference"
            if kind == "budget_constraint":
                return "budget_constraint"
            if kind in {"drink_preference", "brand_constraint"}:
                return "drink_preference"
        if field_path and field_path.startswith("interaction_preferences."):
            return "interaction_preference"
        if field_path and field_path.startswith("budget_preferences."):
            return "budget_preference"
        if route == "memory":
            return "budget_constraint"
        return "drink_preference"

    def _build_messages(self, messages: list[dict[str, Any]], rule_facts: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
        user_lines = []
        for message in messages:
            content = str(message.get("content") or "").strip()
            if content:
                user_lines.append(content)
        rule_hint = ""
        if rule_facts:
            compact = [
                {
                    "fact_type": fact.get("fact_type"),
                    "route": fact.get("route"),
                    "field_path": fact.get("field_path"),
                    "value": fact.get("value"),
                    "normalized_fact": fact.get("normalized_fact"),
                }
                for fact in rule_facts
            ]
            rule_hint = f"\n已由规则层识别到的事实（仅供参考，不要重复输出同义事实）：{json.dumps(compact, ensure_ascii=False)}"
        return [
            {"role": "system", "content": STRUCTURED_EXTRACTION_PROMPT},
            {
                "role": "user",
                "content": "用户原话：\n" + "\n".join(user_lines) + rule_hint,
            },
        ]

    def _extract_via_llm(self, messages: list[dict[str, Any]], rule_facts: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        client = self._create_client()
        response = None
        settings = get_settings()
        breaker = get_circuit_breaker(
            "llm.memory_extraction",
            failure_threshold=settings.dependency_circuit_failure_threshold,
            recovery_timeout_seconds=settings.dependency_circuit_recovery_seconds,
        )
        for response_format in (STRICT_RESPONSE_FORMAT, {"type": "json_object"}, None):
            try:
                breaker.before_call()
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "messages": self._build_messages(messages, rule_facts),
                    "temperature": 0,
                    "extra_body": {"enable_thinking": False},
                }
                if response_format is not None:
                    kwargs["response_format"] = response_format
                response = client.chat.completions.create(**kwargs)
                usage = extract_usage_tokens(response)
                if usage:
                    record_usage(
                        user_id=self.user_id,
                        model=self.model,
                        input_tokens=usage[0],
                        output_tokens=usage[1],
                        usage_kind="memory_extraction",
                    )
                breaker.on_success()
                break
            except Exception as exc:
                breaker.on_failure()
                error = classify_dependency_error(exc, "llm.memory_extraction")
                logger.warning(
                    json.dumps(
                        {
                            "event": "memory_structured_extraction_llm_error",
                            "model": self.model,
                            "error": str(error),
                            "error_category": error.category,
                        },
                        ensure_ascii=False,
                    )
                )
        if response is None:
            return []

        raw_content = response.choices[0].message.content if response.choices else ""
        text = self._strip_code_fence(self._extract_text_content(raw_content))
        parsed = json.loads(text)
        facts = parsed.get("facts", [])
        return [fact for fact in facts if isinstance(fact, dict)]

    @staticmethod
    def _extract_price(text: str) -> float | int | None:
        match = _PRICE_PATTERN.search(text)
        if not match:
            return None
        number = float(match.group("value"))
        return int(number) if number.is_integer() else round(number, 1)

    def _heuristic_extract(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        for message in messages:
            content = str(message.get("content") or "").strip()
            if not content:
                continue

            preferred_categories: list[str] = []
            preferred_brands: list[str] = []
            clauses = [clause.strip() for clause in _CLAUSE_SPLIT_PATTERN.split(content) if clause.strip()]
            for clause in clauses or [content]:
                categories = [normalized for label, normalized in _CATEGORY_MAP.items() if label in clause]
                brands = canonicalize_brand_names(brand for brand in known_brand_names() if brand in clause)
                has_preference = any(hint in clause for hint in _PREFERENCE_HINTS)
                has_avoidance = any(hint in clause for hint in _AVOID_HINTS)
                has_transient = any(hint in clause for hint in _TRANSIENT_HINTS)
                price = self._extract_price(clause)

                if has_preference and categories:
                    for category in categories:
                        if category not in preferred_categories:
                            preferred_categories.append(category)
                if has_preference and brands and not has_avoidance:
                    for brand in brands:
                        if brand not in preferred_brands:
                            preferred_brands.append(brand)
                if has_avoidance and brands:
                    for brand in brands:
                        facts.append(
                            {
                                "fact_type": "drink_preference",
                                "route": "memory",
                                "memory_type": "constraint",
                                "memory_scope": "recommendation",
                                "content": f"近期避免推荐{brand}",
                                "confidence": 0.74,
                                "ttl_days": 30,
                                "normalized_fact": {
                                    "kind": "brand_constraint",
                                    "constraint_type": "brand",
                                    "preference": "avoid",
                                    "value": brand,
                                    "time_scope": "recent" if has_transient else "unspecified",
                                },
                            }
                        )
                if price is not None and ("以内" in clause or "不要超过" in clause or "别超过" in clause):
                    if has_transient:
                        facts.append(
                            {
                                "fact_type": "budget_constraint",
                                "route": "memory",
                                "memory_type": "constraint",
                                "memory_scope": "recommendation",
                                "content": f"最近预算偏紧，尽量控制在{price}元以内",
                                "confidence": 0.8,
                                "ttl_days": 45,
                                "normalized_fact": {
                                    "kind": "budget_constraint",
                                    "constraint_type": "price",
                                    "preference": "lower_price",
                                    "time_scope": "recent",
                                    "soft_price_ceiling": price,
                                },
                            }
                        )
                    else:
                        facts.append(
                            {
                                "fact_type": "budget_preference",
                                "route": "profile",
                                "field_path": "budget_preferences.soft_price_ceiling",
                                "value": price,
                                "content": f"预算偏好：{price} 元以内",
                                "confidence": 0.82,
                                "normalized_fact": {
                                    "kind": "budget_preference",
                                    "field": "soft_price_ceiling",
                                    "value": price,
                                },
                            }
                        )

            if preferred_categories:
                facts.append(
                    {
                        "fact_type": "drink_preference",
                        "route": "profile",
                        "field_path": "drink_preferences.preferred_categories",
                        "value": preferred_categories,
                        "content": f"偏好品类：{', '.join(preferred_categories)}",
                        "confidence": 0.78,
                        "normalized_fact": {
                            "kind": "drink_preference",
                            "field": "preferred_categories",
                            "value": preferred_categories,
                        },
                    }
                )
            if preferred_brands:
                facts.append(
                    {
                        "fact_type": "drink_preference",
                        "route": "profile",
                        "field_path": "drink_preferences.preferred_brands",
                        "value": preferred_brands,
                        "content": f"偏好品牌：{', '.join(preferred_brands)}",
                        "confidence": 0.78,
                        "normalized_fact": {
                            "kind": "drink_preference",
                            "field": "preferred_brands",
                            "value": preferred_brands,
                        },
                    }
                )
        return facts

    def extract_facts(
        self,
        messages: list[dict[str, Any]],
        *,
        rule_facts: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        try:
            if self.api_key:
                facts = self._extract_via_llm(messages, rule_facts=rule_facts)
        except Exception as exc:
            logger.warning(
                json.dumps(
                    {
                        "event": "memory_structured_extraction_llm_parse_error",
                        "model": self.model,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )
            )
        if not facts:
            facts = self._heuristic_extract(messages)

        normalized: list[dict[str, Any]] = []
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            route = str(fact.get("route") or "")
            if route not in {"profile", "memory"}:
                continue
            raw_content = str(fact.get("content") or "").strip()
            field_path = str(fact.get("field_path") or "")
            value = fact.get("value")
            if route == "profile":
                if not field_path:
                    continue
                value = self._normalize_field_value(field_path, value)
            normalized_fact = fact.get("normalized_fact")
            if not isinstance(normalized_fact, dict):
                normalized_fact = self._infer_normalized_fact(route, field_path or None, value, raw_content)
            normalized.append(
                {
                    "fact_type": str(fact.get("fact_type") or self._infer_fact_type(route, field_path or None, normalized_fact)),
                    "route": route,
                    "field_path": field_path or None,
                    "value": value,
                    "memory_type": "constraint" if route == "memory" else fact.get("memory_type"),
                    "memory_scope": "recommendation" if route == "memory" else fact.get("memory_scope"),
                    "normalized_fact": normalized_fact if isinstance(normalized_fact, dict) else None,
                    "content": raw_content,
                    "confidence": float(fact.get("confidence") or 0.7),
                    "ttl_days": int(fact["ttl_days"]) if fact.get("ttl_days") is not None else 30 if route == "memory" else None,
                }
            )
        return normalized
