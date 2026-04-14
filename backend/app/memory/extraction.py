"""记忆提取模块。

本模块负责从用户对话消息中提取偏好和约束，分为两类：

1. 规则提取（rule-based）: 基于关键词匹配快速提取明确的偏好
   - 回复风格偏好（简短/详细）
   - 饮品偏好（糖度、冰度）
   - 预算偏好（价格上限、敏感度）

2. 结构化提取（LLM-based）: 使用 LLM 提取复杂的隐含偏好
   - 多意图解析
   - 隐含约束提取
   - 复杂偏好推断

提取结果分为两个路由：
- profile: 稳定的用户偏好，持久化到用户画像
- memory: 阶段性约束，持久化到记忆项（有 TTL）
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import get_settings
from app.memory.profile import apply_profile_updates
from app.memory import repository
from app.services.memory_structured_extractor import MemoryStructuredExtractorService
from app.services.memory_vectors import MemoryVectorService

# 回复风格关键词：检测用户偏好简短回复
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

# 默认糖度关键词：用于提取用户的默认糖度偏好
_DEFAULT_SUGAR_KEYWORDS = ("少糖", "无糖", "半糖")

# 默认冰度关键词：用于提取用户的默认冰度偏好
_DEFAULT_ICE_KEYWORDS = ("少冰", "去冰", "常温", "热的")

# 临时预算关键词：暗示预算约束是短期的
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

# 阶段性预算关键词：暗示用户当前预算紧张
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

# 稳定预算关键词：暗示预算偏好是长期的
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

# 价格提取正则：匹配 "XX元" 格式的价格
_PRICE_PATTERN = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*元")

# 结构化意图关键词：触发 LLM 结构化提取
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

_TEMPORARY_PREFERENCE_HINTS = (
    "想喝",
    "想来点",
    "想试试",
    "喝点",
    "喝些",
)

_CATEGORY_VALUE_MAP = {
    "果茶": "fruit_tea",
    "水果茶": "fruit_tea",
    "鲜果茶": "fruit_tea",
    "奶茶": "milk_tea",
    "轻乳茶": "light_milk_tea",
    "纯茶": "pure_tea",
    "柠檬茶": "lemon_tea",
    "咖啡": "coffee",
}


def _ttl(days: int | None = None) -> datetime:
    """计算记忆项的过期时间（TTL）。

    参数:
        days: 过期天数，默认使用配置中的 memory_item_default_ttl_days（90天）。

    返回:
        过期时间的 datetime 对象（UTC 时区）。
    """
    days = max(int(days or get_settings().memory_item_default_ttl_days or 90), 1)
    return datetime.now(UTC) + timedelta(days=days)


def _message_text(message: dict[str, Any]) -> str:
    """从消息字典中提取文本内容。

    参数:
        message: 消息字典，包含 'content' 字段。

    返回:
        去除首尾空白的文本内容，如果无内容则返回空字符串。
    """
    return str(message.get("content") or "").strip()


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    """检查文本是否包含任意一个关键词。

    参数:
        text: 要检查的文本。
        keywords: 关键词元组。

    返回:
        如果包含任意关键词返回 True，否则返回 False。
    """
    return any(keyword in text for keyword in keywords)


def _as_price_value(text: str) -> float | int | None:
    """从文本中提取价格数值。

    使用正则匹配 "XX元" 格式，提取数值部分。

    参数:
        text: 包含价格的文本。

    返回:
        提取的价格值（整数或浮点数），如果未匹配则返回 None。
    """
    match = _PRICE_PATTERN.search(text)
    if not match:
        return None
    raw = float(match.group("value"))
    # 整数返回 int，否则保留一位小数
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
    """构建标准化的偏好事实字典。

    事实（fact）是提取结果的基本单元，包含类型、路由、内容、置信度等。

    参数:
        fact_type: 事实类型，如 "drink_preference"、"budget_constraint"。
        route: 路由目标，"profile" 或 "memory"。
        field_path: 字段路径，如 "drink_preferences.default_sugar"。
        value: 提取的值。
        scope: 作用域，"profile" 或 "memory"。
        content: 事实的自然语言描述。
        normalized_fact: 结构化的规范化事实。
        confidence: 置信度（0-1）。
        ttl_days: 过期天数（仅 memory 路由）。
        memory_type: 记忆类型，如 "constraint"。
        memory_scope: 记忆作用域，如 "recommendation"。
        source_message: 来源消息字典。
        source_ref: 来源引用（通常是 thread_key）。

    返回:
        标准化的事实字典。
    """
    fact: dict[str, Any] = {
        "fact_type": fact_type,
        "route": route,
        "content": content,
        "confidence": confidence,
    }
    # 可选字段：仅在有值时添加
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
    # 来源信息：用于追溯
    if source_message:
        if source_message.get("id") is not None:
            fact["source_message_id"] = str(source_message["id"])
        if source_message.get("created_at") is not None:
            fact["source_message_created_at"] = source_message["created_at"]
    if source_ref is not None:
        fact["source_ref"] = source_ref
    return fact


def _message_has_multi_intent_hint(content: str) -> bool:
    """检查消息是否包含多意图提示词。

    多意图提示词如 "同时"、"另外" 等，暗示一条消息包含多个偏好。

    参数:
        content: 消息内容。

    返回:
        如果包含多意图提示词返回 True。
    """
    return _contains_any(content, ("同时", "另外", "以及", "并且", "还有"))


def _needs_structured_intent_parse(content: str) -> bool:
    """检查消息是否需要结构化意图解析。

    包含 "喜欢"、"偏好"、"别推荐" 等关键词的消息通常需要 LLM 深度解析。

    参数:
        content: 消息内容。

    返回:
        如果需要结构化解析返回 True。
    """
    return _contains_any(content, _STRUCTURED_INTENT_HINTS)


def _extract_reply_style_facts(content: str, message: dict[str, Any], thread_key: str) -> list[dict[str, Any]]:
    """提取回复风格偏好事实。

    检测用户是否偏好简短回复，如 "简单点"、"别啰嗦" 等。

    参数:
        content: 消息内容。
        message: 来源消息字典。
        thread_key: 会话线程标识。

    返回:
        提取的事实列表，如果未检测到则返回空列表。
    """
    if not _contains_any(content, _REPLY_STYLE_KEYWORDS):
        return []
    # 检测到简短风格偏好，构建事实
    return [
        _build_fact(
            fact_type="interaction_preference",
            route="profile",
            field_path="interaction_preferences.reply_style",
            value="brief",
            scope="profile",
            content="回答风格偏好：简短",
            normalized_fact={"kind": "interaction_preference", "field": "reply_style", "value": "brief"},
            confidence=0.95,  # 关键词匹配置信度较高
            source_message=message,
            source_ref=thread_key,
        )
    ]


def _extract_drink_facts(content: str, message: dict[str, Any], thread_key: str) -> list[dict[str, Any]]:
    """提取饮品偏好事实。

    检测用户的默认糖度和冰度偏好，如 "少糖"、"去冰" 等。

    参数:
        content: 消息内容。
        message: 来源消息字典。
        thread_key: 会话线程标识。

    返回:
        提取的事实列表，可能包含糖度和/或冰度偏好。
    """
    facts: list[dict[str, Any]] = []
    
    # 提取糖度偏好
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
                break  # 只取第一个匹配的糖度
    
    # 提取冰度偏好
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
                break  # 只取第一个匹配的冰度
    return facts


def _extract_temporary_category_preference_facts(content: str, message: dict[str, Any], thread_key: str) -> list[dict[str, Any]]:
    """提取阶段性饮品品类偏好事实。

    适配「这段时间我想喝果茶类」这类明确带时间范围的短期偏好，
    优先写入 memory item，而不是长期 profile。
    """
    if not _contains_any(content, _TEMPORARY_BUDGET_HINTS):
        return []
    if not _contains_any(content, _TEMPORARY_PREFERENCE_HINTS):
        return []

    matched_label = next((label for label in _CATEGORY_VALUE_MAP if label in content), None)
    if not matched_label:
        return []

    normalized_value = _CATEGORY_VALUE_MAP[matched_label]
    return [
        _build_fact(
            fact_type="drink_preference",
            route="memory",
            scope="memory",
            memory_type="preference",
            memory_scope="recommendation",
            content=f"近期更想喝{matched_label}类饮品",
            normalized_fact={
                "kind": "drink_preference",
                "field": "preferred_categories",
                "value": [normalized_value],
                "time_scope": "recent",
            },
            confidence=0.9,
            ttl_days=30,
            source_message=message,
            source_ref=thread_key,
        )
    ]


def _extract_budget_facts(content: str, message: dict[str, Any], thread_key: str) -> list[dict[str, Any]]:
    """提取预算偏好或约束事实。

    根据关键词判断是稳定预算偏好还是阶段性预算约束：
    - 稳定偏好：包含 "通常"、"一般" 等词，路由到 profile
    - 阶段约束：包含 "最近"、"暂时" 等词，路由到 memory（带 TTL）

    参数:
        content: 消息内容。
        message: 来源消息字典。
        thread_key: 会话线程标识。

    返回:
        提取的事实列表，可能包含价格上限和/或价格敏感度。
    """
    facts: list[dict[str, Any]] = []
    
    # 检测是否有预算相关信号
    has_budget_signal = "预算" in content or "价格" in content or "便宜" in content or "元" in content
    if not has_budget_signal:
        return facts

    # 提取价格数值
    ceiling = _as_price_value(content)
    # 判断是阶段性约束还是稳定偏好
    stage_hint = _contains_any(content, _TEMPORARY_BUDGET_HINTS) or _contains_any(content, _STAGE_BUDGET_HINTS)
    stable_hint = _contains_any(content, _PROFILE_BUDGET_HINTS)

    # 情况1：稳定预算偏好（有价格、有稳定关键词、无临时关键词）
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
        # 如果还提到 "便宜" 或 "价格敏感"，额外记录价格敏感度
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

    # 情况2：阶段性预算约束（路由到 memory，带 TTL）
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
                ttl_days=45,  # 阶段性约束默认 45 天过期
                source_message=message,
                source_ref=thread_key,
            )
        )
    return facts


def extract_rule_based_facts(messages: list[dict[str, Any]], thread_key: str) -> list[dict[str, Any]]:
    """基于规则从消息列表中提取偏好事实。

    遍历所有消息，依次提取回复风格、饮品偏好、预算偏好。
    这是快速提取路径，不依赖 LLM。

    参数:
        messages: 消息字典列表。
        thread_key: 会话线程标识。

    返回:
        提取的事实列表。
    """
    facts: list[dict[str, Any]] = []
    for message in messages:
        content = _message_text(message)
        if not content:
            continue
        # 依次提取各类偏好
        facts.extend(_extract_reply_style_facts(content, message, thread_key))
        facts.extend(_extract_drink_facts(content, message, thread_key))
        facts.extend(_extract_temporary_category_preference_facts(content, message, thread_key))
        facts.extend(_extract_budget_facts(content, message, thread_key))
    return facts


def extract_structured_facts(
    messages: list[dict[str, Any]],
    *,
    user_id: str | None = None,
    thread_key: str,
    rule_facts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """使用 LLM 结构化提取偏好事实。

    调用 MemoryStructuredExtractorService 进行深度提取，
    可以处理复杂的隐含偏好和多意图。

    参数:
        messages: 消息字典列表。
        user_id: 用户标识符。
        thread_key: 会话线程标识。
        rule_facts: 规则提取的事实，可作为 LLM 提取的参考。

    返回:
        提取的事实列表。
    """
    source_message = messages[-1] if messages else None
    # 调用 LLM 结构化提取服务
    extracted = MemoryStructuredExtractorService(user_id=user_id).extract_facts(messages, rule_facts=rule_facts)
    facts: list[dict[str, Any]] = []
    for fact in extracted:
        facts.append(
            _build_fact(
                fact_type=str(fact.get("fact_type") or "drink_preference"),
                route=str(fact.get("route") or "profile"),
                field_path=fact.get("field_path"),
                value=fact.get("value"),
                scope="profile" if str(fact.get("route") or "profile") == "profile" else "memory",
                content=str(fact.get("content") or "")[:240],  # 限制内容长度
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


def _call_structured_extractor(
    messages: list[dict[str, Any]],
    *,
    user_id: str,
    thread_key: str,
    rule_facts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """调用结构化提取器，兼容旧版签名。

    封装 extract_structured_facts，处理测试替身可能缺少 user_id 参数的情况。

    参数:
        messages: 消息字典列表。
        user_id: 用户标识符。
        thread_key: 会话线程标识。
        rule_facts: 规则提取的事实。

    返回:
        提取的事实列表。
    """
    try:
        return extract_structured_facts(
            messages,
            user_id=user_id,
            thread_key=thread_key,
            rule_facts=rule_facts,
        )
    except TypeError:
        # 测试替身可能暴露不带 user_id 的旧版签名
        return extract_structured_facts(
            messages,
            thread_key=thread_key,
            rule_facts=rule_facts,
        )


def _build_memory_vector_service(user_id: str) -> MemoryVectorService:
    """构建记忆向量服务实例，兼容测试替身。

    参数:
        user_id: 用户标识符。

    返回:
        MemoryVectorService 实例。
    """
    try:
        return MemoryVectorService(user_id=user_id)
    except TypeError:
        # 测试替身可能不接受构造函数参数
        return MemoryVectorService()


def _needs_structured_extraction(content: str, message_facts: list[dict[str, Any]]) -> bool:
    """判断消息是否需要结构化提取。

    触发条件：
    1. 包含结构化意图关键词（"喜欢"、"偏好" 等）
    2. 单条消息提取了 3 个以上事实
    3. 提取了 2 个以上事实且包含多意图提示词

    参数:
        content: 消息内容。
        message_facts: 该消息已提取的事实列表。

    返回:
        如果需要结构化提取返回 True。
    """
    # 条件1：包含结构化意图关键词
    if _needs_structured_intent_parse(content):
        return True
    # 条件2：事实数量多，可能需要深度解析
    if len(message_facts) >= 3:
        return True
    # 条件3：多意图提示
    if len(message_facts) >= 2 and _message_has_multi_intent_hint(content):
        return True
    return False


def _freeze_value(value: Any) -> Any:
    """将值冻结为可哈希的不可变形式。

    用于事实去重，将字典和列表转换为元组。

    参数:
        value: 要冻结的值。

    返回:
        可哈希的不可变值。
    """
    if isinstance(value, dict):
        # 字典转为排序后的键值对元组
        return tuple(sorted((key, _freeze_value(inner)) for key, inner in value.items()))
    if isinstance(value, list):
        # 列表转为元组
        return tuple(_freeze_value(item) for item in value)
    return value  # 基本类型本身就是可哈希的


def _merge_profile_updates(profile_updates: dict[str, Any], fact: dict[str, Any]) -> None:
    """将事实合并到画像更新字典中。

    根据 field_path 解析分区和字段名，更新 profile_updates。
    例如 "drink_preferences.default_sugar" -> profile_updates["drink_preferences"]["default_sugar"]

    参数:
        profile_updates: 画像更新字典（会被原地修改）。
        fact: 包含 field_path 和 value 的事实字典。
    """
    field_path = str(fact.get("field_path") or "")
    value = fact.get("value")
    if not field_path:
        return
    # 解析字段路径：section.leaf
    section, _, leaf = field_path.partition(".")
    if not section or not leaf:
        return
    # 原地更新字典
    target = profile_updates.setdefault(section, {})
    if isinstance(target, dict):
        target[leaf] = value


def _merge_and_resolve_facts(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """合并并解析事实列表，生成最终提取结果。

    执行去重、路由分发和结构化输出：
    - canonical_facts: 去重后的事实列表
    - profile_updates: 合并后的画像更新
    - memory_upserts: 记忆项更新列表
    - diagnostics: 诊断信息

    参数:
        facts: 事实列表（可能包含重复）。

    返回:
        包含 canonical_facts、profile_updates、memory_upserts 和 diagnostics 的结果字典。
    """
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

    # 使用冻结的身份元组进行去重
    seen_facts: set[tuple[Any, ...]] = set()
    for fact in facts:
        route = str(fact.get("route") or "")
        # 构建事实身份元组用于去重
        identity = (
            route,
            fact.get("fact_type"),
            fact.get("field_path"),
            _freeze_value(fact.get("value")),
            fact.get("scope"),
            _freeze_value(fact.get("normalized_fact") or {}),
        )
        if identity in seen_facts:
            continue  # 跳过重复事实
        seen_facts.add(identity)
        result["canonical_facts"].append(fact)
        
        # 根据路由分发到不同输出
        if route == "profile":
            # 路由到画像更新
            _merge_profile_updates(result["profile_updates"], fact)
            result["diagnostics"]["profile_fact_count"] += 1
        elif route == "memory":
            # 路由到记忆项更新
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
    """构建记忆提取结果。

    从最近的用户消息中提取偏好和约束，执行流程：
    1. 获取最近 10 条用户消息
    2. 对每条消息执行规则提取
    3. 判断是否需要结构化提取（LLM）
    4. 合并并去重所有事实
    5. 生成诊断信息

    参数:
        user_id: 用户标识符。
        thread_key: 会话线程标识。

    返回:
        包含 canonical_facts、profile_updates、memory_upserts 和 diagnostics 的结果字典。
    """
    # 获取最近 10 条用户消息（按时间正序）
    recent_messages = list(reversed(repository.list_recent_user_messages(user_id, thread_key, limit=10)))

    rule_facts: list[dict[str, Any]] = []
    structured_facts: list[dict[str, Any]] = []
    structured_error_count = 0

    # 遍历每条消息进行提取
    for message in recent_messages:
        content = _message_text(message)
        if not content:
            continue
        # 规则提取（快速路径）
        message_rule_facts = extract_rule_based_facts([message], thread_key)
        rule_facts.extend(message_rule_facts)
        
        # 判断是否需要结构化提取
        if _needs_structured_extraction(content, message_rule_facts):
            try:
                message_structured_facts = _call_structured_extractor(
                    [message],
                    user_id=user_id,
                    thread_key=thread_key,
                    rule_facts=message_rule_facts,
                )
            except Exception:
                # 结构化提取失败，记录错误
                structured_error_count += 1
                message_structured_facts = []
            structured_facts.extend(message_structured_facts)

    # 合并规则提取和结构化提取的事实
    canonical_facts = rule_facts + structured_facts
    result = _merge_and_resolve_facts(canonical_facts)
    
    # 填充诊断信息
    result["diagnostics"]["rule_fact_count"] = len(rule_facts)
    result["diagnostics"]["structured_fact_count"] = len(structured_facts)
    result["diagnostics"]["structured_error_count"] = structured_error_count
    result["diagnostics"]["canonical_fact_count"] = len(result["canonical_facts"])
    result["diagnostics"]["user_message_count"] = len(recent_messages)
    return result


def persist_extraction_result(user_id: str, thread_key: str) -> dict[str, Any]:
    """持久化记忆提取结果。

    执行流程：
    1. 调用 build_extraction_result 构建提取结果
    2. 应用画像更新到数据库
    3. 写入记忆项到数据库和向量存储
    4. 返回包含持久化详情的结果

    通常在对话结束后通过 memory job 调用。

    参数:
        user_id: 用户标识符。
        thread_key: 会话线程标识。

    返回:
        包含提取结果和持久化详情的字典，包括 created_memory_items。
    """
    # 构建提取结果
    result = build_extraction_result(user_id, thread_key)

    # 应用画像更新
    profile_updates = result.get("profile_updates") or {}
    if profile_updates:
        result["profile"] = apply_profile_updates(user_id, profile_updates)

    # 持久化记忆项
    upserted: list[dict[str, Any]] = []
    if result.get("memory_upserts"):
        vector_service = _build_memory_vector_service(user_id)
        for candidate in result["memory_upserts"]:
            # 写入数据库
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
            # 写入向量存储
            vector_service.upsert_memory_item(item)
            upserted.append(item)
    
    # 记录持久化结果
    result["created_memory_items"] = upserted
    result["diagnostics"]["memory_upsert_count"] = len(upserted)
    result["diagnostics"]["profile_update_count"] = len(profile_updates)
    return result


def extract_candidate_memories(user_id: str, thread_key: str) -> list[dict[str, Any]]:
    """提取候选记忆项（不持久化）。

    仅构建提取结果，返回记忆项候选列表，不写入数据库。
    用于预览或调试。

    参数:
        user_id: 用户标识符。
        thread_key: 会话线程标识。

    返回:
        记忆项候选列表。
    """
    result = build_extraction_result(user_id, thread_key)
    return list(result.get("memory_upserts") or [])


def persist_candidate_memories(user_id: str, thread_key: str) -> list[dict[str, Any]]:
    """持久化候选记忆项。

    调用 persist_extraction_result 并返回已创建的记忆项列表。
    这是 memory job 的主要入口之一。

    参数:
        user_id: 用户标识符。
        thread_key: 会话线程标识。

    返回:
        已创建的记忆项列表。
    """
    result = persist_extraction_result(user_id, thread_key)
    return list(result.get("created_memory_items") or [])
