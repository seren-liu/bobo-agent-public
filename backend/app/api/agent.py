from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import re
from datetime import datetime, timedelta
from uuid import uuid4
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from app.agent.graph import stream_agent_events
from app.agent.state import audit_agent_event
from app.core.brands import canonicalize_brand_name, known_brand_names
from app.core.rate_limit import enforce_rate_limit
from app.core.threads import normalize_session_thread_id
from app.memory.jobs import enqueue_memory_job
from app.memory import repository
from app.observability import observe_agent_budget_check, observe_agent_chat, observe_agent_first_token, observe_agent_tool_call, observe_fast_path, observe_task_execution
from app.services.llm_budget import (
    affordable_output_tokens,
    cost_cny_for_tokens,
    current_budget_date,
    daily_budget_cny,
    embedding_model,
    estimate_tokens,
    extract_usage_tokens,
    input_cost_cny,
    memory_embedding_reserve_tokens,
    memory_extraction_reserve_tokens,
    remaining_output_token_budget,
    record_usage,
    resolve_model_pricing,
    supports_pricing,
)
from app.tooling.operations import (
    get_day_impl,
    get_menu_brand_coverage_impl,
    get_recent_records_impl,
    get_stats_impl,
    search_menu_impl,
)

router = APIRouter(prefix="/bobo/agent", tags=["agent"])
logger = logging.getLogger("bobo.agent.api")


class ChatRequest(BaseModel):
    """Agent 聊天接口的请求模型。

    属性:
        message: 用户消息内容，不能为空。
        thread_id: 会话线程标识符，用于对话连续性。
        user_id: 已弃用的遗留字段；请使用 request.state.user_id。
        max_steps: Agent 推理最大步数（1-30，默认 10）。
    """

    message: str = Field(min_length=1)
    thread_id: str
    user_id: str | None = Field(default=None, description="Deprecated legacy fallback; request.state.user_id takes precedence.")
    max_steps: int = Field(default=10, ge=1, le=30)


def _log_phase(request_id: str, phase: str, **fields: Any) -> None:
    """以结构化 JSON 格式记录 Agent 聊天阶段事件。

    参数:
        request_id: 请求的唯一标识符。
        phase: 正在记录的执行阶段名称。
        **fields: 要包含在日志条目中的额外键值对。
    """
    logger.info(
        json.dumps(
            {
                "event": "agent_chat_phase",
                "request_id": request_id,
                "phase": phase,
                **fields,
            },
            ensure_ascii=False,
            default=str,
        )
    )


def _extract_budget_ceiling(message: str) -> int | None:
    """从用户消息中提取预算上限金额。

    解析中文自然语言模式，如 "20元以下"、"不超过15元" 等。

    参数:
        message: 要解析的用户消息文本。

    返回:
        提取的预算金额（人民币），如果未找到预算模式则返回 None。
    """
    patterns = [
        r"(\d+)\s*元以下",
        r"不超过\s*(\d+)\s*元",
        r"(\d+)\s*元以内",
        r"预算\s*(\d+)\s*元",
    ]
    for pattern in patterns:
        matched = re.search(pattern, message)
        if matched:
            return int(matched.group(1))
    return None


def _extract_brand(message: str) -> str | None:
    """从用户消息中提取品牌名称。

    首先检查已知品牌名称，然后尝试正则模式匹配自然语言品牌引用。

    参数:
        message: 要解析的用户消息文本。

    返回:
        规范化的品牌名称，如果未检测到品牌则返回 None。
    """
    for candidate in known_brand_names():
        if candidate and candidate in message:
            return canonicalize_brand_name(candidate)
    normalized = re.sub(r"\s+", "", message)
    patterns = (
        r"(?:推荐一杯|推荐|来一杯|来个|找|喝)\s*([^\d，。！？!?,,]{2,12})的(奶茶|果茶|轻乳茶|纯茶|柠檬茶|咖啡)",
        r"([^\d，。！？!?,,]{2,12})的(奶茶|果茶|轻乳茶|纯茶|柠檬茶|咖啡)",
    )
    for raw_pattern in patterns:
        pattern = re.search(raw_pattern, normalized)
        if not pattern:
            continue
        candidate = pattern.group(1)
        for token in ("今天请", "今天", "请给我", "给我", "帮我", "想喝"):
            candidate = candidate.replace(token, "")
        candidate = candidate.strip("的呀吧呢吗")
        if candidate and len(candidate) >= 2:
            return canonicalize_brand_name(candidate)
    return None


def _extract_menu_query(message: str) -> str | None:
    """从用户消息中提取饮品类别查询。

    检测预定义的饮品类别，如果茶、奶茶、轻乳茶等。

    参数:
        message: 要解析的用户消息文本。

    返回:
        检测到的饮品类别，如果未找到类别则返回 None。
    """
    if "果茶" in message:
        return "果茶"
    if "奶茶" in message:
        return "奶茶"
    if "轻乳茶" in message:
        return "轻乳茶"
    if "纯茶" in message:
        return "纯茶"
    if "柠檬茶" in message:
        return "柠檬茶"
    if "咖啡" in message:
        return "咖啡"
    return None


def _normalize_menu_message(message: str, brand: str | None = None) -> str:
    """规范化菜单请求消息，移除常见填充词。

    去除空格、常见动作动词，以及可选的品牌名称，
    以提取核心菜单查询。

    参数:
        message: 要规范化的用户消息文本。
        brand: 可选的品牌名称，将从消息中移除。

    返回:
        移除填充词后的规范化消息字符串。
    """
    normalized = re.sub(r"\s+", "", message)
    for token in ("给我", "推荐", "一下", "一杯", "喝什么", "想喝", "给我来", "帮我"):
        normalized = normalized.replace(token, "")
    if brand:
        normalized = normalized.replace(brand, "")
    return normalized.strip("的呀吧呢吗，。！ ")


def _menu_category_aliases(query: str | None) -> list[str]:
    """获取饮品类别查询的别名术语。

    返回可用于更广泛菜单搜索的相关术语列表。

    参数:
        query: 要获取别名的饮品类别。

    返回:
        别名术语列表，如果 query 为 None 或未知则返回空列表。
    """
    aliases = {
        "奶茶": ["奶茶", "牛乳茶", "乳茶", "厚乳", "奶香", "经典奶茶", "招牌奶茶"],
        "果茶": ["果茶", "鲜果茶", "水果茶", "果饮", "果香", "柠檬茶"],
        "轻乳茶": ["轻乳茶", "乳茶", "奶茶"],
        "纯茶": ["纯茶", "茗茶", "原叶茶"],
        "柠檬茶": ["柠檬茶", "果茶", "鲜果茶"],
        "咖啡": ["咖啡", "拿铁", "美式"],
    }
    if not query:
        return []
    return aliases.get(query, [query])


def _build_menu_query_candidates(message: str, brand: str, query: str | None) -> list[str]:
    """构建优先级排序的菜单搜索查询候选列表。

    组合规范化消息、提取的查询、类别别名和品牌前缀查询，
    实现全面的菜单搜索。

    参数:
        message: 原始用户消息。
        brand: 目标品牌名称。
        query: 提取的饮品类别查询。

    返回:
        去重后的搜索查询候选列表，按优先级排序。
    """
    candidates: list[str] = []
    normalized = _normalize_menu_message(message, brand)
    if normalized:
        candidates.append(normalized)
    if query:
        candidates.append(query)
    candidates.extend(_menu_category_aliases(query))
    if query:
        candidates.append(f"{brand}{query}")
    return list(dict.fromkeys(item.strip() for item in candidates if item and item.strip()))


def _menu_keyword_bonus(item: dict[str, Any], query: str | None) -> float:
    """基于关键词匹配计算相关性加分。

    为菜单项名称和描述中匹配的类别特定关键词分配加权分数。

    参数:
        item: 包含 'name' 和 'description' 字段的菜单项字典。
        query: 要匹配的饮品类别。

    返回:
        要添加到项目基础相关性分数的加分值。
    """
    if not query:
        return 0.0
    text = f"{item.get('name') or ''} {item.get('description') or ''}"
    bonus = 0.0
    alias_weights = {
        "奶茶": {"奶茶": 0.7, "牛乳茶": 0.6, "乳茶": 0.5, "奶香": 0.35, "厚乳": 0.3},
        "果茶": {"果茶": 0.7, "鲜果": 0.6, "水果": 0.6, "柠檬": 0.35, "果香": 0.3},
        "轻乳茶": {"轻乳茶": 0.7, "乳茶": 0.5, "奶茶": 0.4},
        "纯茶": {"纯茶": 0.7, "原叶": 0.5, "茗茶": 0.4},
        "柠檬茶": {"柠檬茶": 0.7, "柠檬": 0.5, "果茶": 0.2},
        "咖啡": {"咖啡": 0.7, "拿铁": 0.5, "美式": 0.45},
    }
    for token, weight in alias_weights.get(query, {query: 0.4}).items():
        if token in text:
            bonus += weight
    return bonus


def _merge_menu_results(result_sets: list[list[dict[str, Any]]], query: str | None, max_price: int | None) -> list[dict[str, Any]]:
    """合并多个菜单搜索结果集，去重并评分。

    组合多个查询的结果，按项目 ID 去重，
    应用关键词加分评分，并按价格约束过滤。

    参数:
        result_sets: 要合并的菜单搜索结果列表。
        query: 用于关键词加分计算的饮品类别。
        max_price: 可选的最高价格过滤（人民币）。

    返回:
        合并、去重并过滤后的菜单项列表。
    """
    merged: dict[str, dict[str, Any]] = {}
    for result_set in result_sets:
        for item in result_set:
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            score = float(item.get("score") or 0) + _menu_keyword_bonus(item, query)
            existing = merged.get(item_id)
            candidate = {**item, "score": score}
            if existing is None or score > float(existing.get("score") or 0):
                merged[item_id] = candidate

    return _filter_menu_results(list(merged.values()), max_price=max_price)


async def _retrieve_menu_fast_path(
    *,
    message: str,
    brand: str,
    query: str | None,
    max_price: int | None,
    user_id: str,
    request_id: str,
    thread_id: str,
) -> dict[str, Any]:
    """执行快速路径菜单检索，采用多查询召回策略。

    并行执行多个查询候选的菜单搜索，合并结果，
    如果品牌无覆盖则回退到跨品牌搜索。

    参数:
        message: 用于构建查询的原始用户消息。
        brand: 搜索的目标品牌名称。
        query: 提取的饮品类别查询。
        max_price: 可选的最高价格过滤。
        user_id: 用于日志记录的用户标识符。
        request_id: 用于追踪的请求标识符。
        thread_id: 会话线程标识符。

    返回:
        包含 'results'、'brand'、'query'、'attempted_queries'、
        'brand_has_coverage' 和 'fallback_results' 字段的字典。
    """
    attempted_queries = _build_menu_query_candidates(message, brand, query)
    search_tasks = [
        search_menu_impl(
            query=candidate,
            brand=brand,
            user_id=user_id,
            request_id=request_id,
            thread_id=thread_id,
            source="agent_fast_path",
        )
        for candidate in attempted_queries[:5]
    ]
    result_sets: list[list[dict[str, Any]]] = []
    if search_tasks:
        payloads = await asyncio.gather(*search_tasks, return_exceptions=True)
        for payload in payloads:
            if isinstance(payload, Exception):
                result_sets.append([])
                continue
            result_sets.append(list(payload.get("results") or []))

    merged_results = _merge_menu_results(result_sets, query, max_price)
    brand_coverage = await asyncio.to_thread(
        get_menu_brand_coverage_impl,
        brand=brand,
        user_id=user_id,
        request_id=request_id,
        thread_id=thread_id,
        source="agent_fast_path",
    )
    brand_has_coverage = brand_coverage if brand_coverage is not None else any(result_sets)

    fallback_results: list[dict[str, Any]] = []
    if not brand_has_coverage and query:
        fallback_payload = await search_menu_impl(
            query=query,
            brand=None,
            user_id=user_id,
            request_id=request_id,
            thread_id=thread_id,
            source="agent_fast_path_fallback",
        )
        fallback_results = _merge_menu_results([list(fallback_payload.get("results") or [])], query, max_price)

    return {
        "results": merged_results,
        "brand": brand,
        "query": query,
        "attempted_queries": attempted_queries[:5],
        "brand_has_coverage": brand_has_coverage,
        "fallback_results": fallback_results,
    }


def _menu_generation_model() -> str:
    """获取用于菜单缺口回复生成的 LLM 模型名称。

    返回:
        从 QWEN_CHAT_MODEL 环境变量获取的模型名称，默认为 'qwen3-32b'。
    """
    return os.getenv("QWEN_CHAT_MODEL") or "qwen3-32b"


def _chat_model() -> str:
    """获取 Agent 聊天使用的主要 LLM 模型名称。

    返回:
        从 QWEN_CHAT_MODEL 环境变量获取的模型名称，默认为 'qwen3-32b'。
    """
    return os.getenv("QWEN_CHAT_MODEL") or "qwen3-32b"


def _daily_budget_snapshot(*, user_id: str, model: str) -> dict[str, Any]:
    """获取用户当前的每日 LLM 预算使用快照。

    检索使用数据、定价信息，并计算剩余预算。

    参数:
        user_id: 要获取预算的用户标识符。
        model: 用于定价查询的 LLM 模型名称。

    返回:
        包含 'usage_date'、'usage'、'pricing'、'budget_cny'、
        'spent_cost_cny'、'remaining_cny' 和 'remaining_output_tokens' 字段的字典。
    """
    usage_date = current_budget_date()
    usage = repository.get_daily_llm_usage(user_id, usage_date, model)
    pricing = resolve_model_pricing(model)
    budget_cny = daily_budget_cny()
    spent_cost_cny = float(usage.get("estimated_cost_cny") or 0.0)
    remaining_cny = max(budget_cny - spent_cost_cny, 0.0)
    return {
        "usage_date": usage_date,
        "usage": usage,
        "pricing": pricing,
        "budget_cny": budget_cny,
        "spent_cost_cny": spent_cost_cny,
        "remaining_cny": remaining_cny,
        "remaining_output_tokens": remaining_output_token_budget(
            pricing=pricing,
            spent_cost_cny=spent_cost_cny,
            daily_budget=budget_cny,
        ),
    }


def _message_budget_reserve(*, user_message: str, chat_model: str) -> dict[str, Any]:
    """计算新聊天消息的预留 token 成本。

    估算聊天提示词、记忆检索嵌入、记忆提取和记忆更新嵌入的成本。

    参数:
        user_message: 要估算 token 的用户消息文本。
        chat_model: 用于定价计算的 LLM 模型名称。

    返回:
        包含每个操作的详细 token 和成本预留的字典，
        以及总计 'reserved_cost_cny'。
    """
    reserves: dict[str, Any] = {
        "chat_prompt_tokens": estimate_tokens(user_message),
        "chat_prompt_cost_cny": 0.0,
        "memory_retrieval_embedding_tokens": 0,
        "memory_retrieval_embedding_cost_cny": 0.0,
        "memory_extraction_input_tokens": 0,
        "memory_extraction_output_tokens": 0,
        "memory_extraction_cost_cny": 0.0,
        "memory_upsert_embedding_tokens": 0,
        "memory_upsert_embedding_cost_cny": 0.0,
    }

    chat_pricing = resolve_model_pricing(chat_model)
    reserves["chat_prompt_cost_cny"] = input_cost_cny(pricing=chat_pricing, input_tokens=reserves["chat_prompt_tokens"])

    embedding = embedding_model()
    if supports_pricing(embedding):
        reserves["memory_retrieval_embedding_tokens"] = estimate_tokens(user_message)
        reserves["memory_retrieval_embedding_cost_cny"] = input_cost_cny(
            pricing=resolve_model_pricing(embedding),
            input_tokens=reserves["memory_retrieval_embedding_tokens"],
        )
        reserves["memory_upsert_embedding_tokens"] = memory_embedding_reserve_tokens()
        reserves["memory_upsert_embedding_cost_cny"] = input_cost_cny(
            pricing=resolve_model_pricing(embedding),
            input_tokens=reserves["memory_upsert_embedding_tokens"],
        )

    extraction_input_tokens, extraction_output_tokens = memory_extraction_reserve_tokens()
    reserves["memory_extraction_input_tokens"] = extraction_input_tokens
    reserves["memory_extraction_output_tokens"] = extraction_output_tokens
    reserves["memory_extraction_cost_cny"] = cost_cny_for_tokens(
        pricing=chat_pricing,
        input_tokens=extraction_input_tokens,
        output_tokens=extraction_output_tokens,
    )
    reserves["reserved_cost_cny"] = round(
        reserves["chat_prompt_cost_cny"]
        + reserves["memory_retrieval_embedding_cost_cny"]
        + reserves["memory_extraction_cost_cny"]
        + reserves["memory_upsert_embedding_cost_cny"],
        6,
    )
    return reserves


def _available_output_tokens_after_reserve(*, snapshot: dict[str, Any], reserve: dict[str, Any]) -> int:
    """计算成本预留后的剩余输出 token 预算。

    参数:
        snapshot: 来自 _daily_budget_snapshot 的预算快照。
        reserve: 来自 _message_budget_reserve 的成本预留。

    返回:
        扣除预留成本后可用的输出 token 数量。
    """
    available_cost_cny = max(snapshot["remaining_cny"] - reserve["reserved_cost_cny"], 0.0)
    return affordable_output_tokens(pricing=snapshot["pricing"], available_cost_cny=available_cost_cny)


def _budget_error_detail(*, snapshot: dict[str, Any]) -> str:
    """生成预算耗尽的用户友好错误消息。

    参数:
        snapshot: 包含使用和定价信息的预算快照。

    返回:
        描述预算耗尽状态的中文错误消息。
    """
    pricing = snapshot["pricing"]
    spent = snapshot["spent_cost_cny"]
    budget = snapshot["budget_cny"]
    remaining_output_tokens = snapshot["remaining_output_tokens"]
    return (
        f"今日 AI 问答预算已用完。当前模型 {pricing.model} 按官方非思考价估算，"
        f"今日已用约 {spent:.3f} / {budget:.2f} 元；剩余可用输出 token 约 {remaining_output_tokens}。"
    )


def _budget_reserve_error_detail(*, snapshot: dict[str, Any], reserve: dict[str, Any], available_output_tokens: int) -> str:
    """生成预算预留不足的用户友好错误消息。

    参数:
        snapshot: 包含使用和定价信息的预算快照。
        reserve: 成本预留详情。
        available_output_tokens: 计算出的可用输出 token。

    返回:
        描述新请求预算不足的中文错误消息。
    """
    pricing = snapshot["pricing"]
    return (
        f"今日 AI 问答预算不足以开启新一轮对话。当前模型 {pricing.model} 按官方非思考价估算，"
        f"今日已用约 {snapshot['spent_cost_cny']:.3f} / {snapshot['budget_cny']:.2f} 元；"
        f"本轮请求预留成本约 {reserve['reserved_cost_cny']:.4f} 元，剩余可用输出 token 约 {available_output_tokens}。"
    )


def _extract_generated_text(raw: Any) -> str:
    """从各种 LLM 响应格式中提取文本内容。

    处理字符串、字典列表和对象格式。

    参数:
        raw: 各种格式的原始 LLM 响应内容。

    返回:
        提取并去除首尾空白的文本字符串。
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        out: list[str] = []
        for chunk in raw:
            if isinstance(chunk, dict):
                out.append(str(chunk.get("text") or ""))
            else:
                out.append(str(getattr(chunk, "text", "") or ""))
        return "".join(out).strip()
    return str(raw).strip()


async def _generate_unstructured_menu_reply(
    *,
    user_message: str,
    brand: str,
    query: str | None,
    max_price: int | None,
) -> tuple[str, tuple[int, int] | None]:
    """当品牌无菜单覆盖时生成回退菜单回复。

    使用 LLM 生成有帮助的回复，但不声称拥有真实菜单数据。
    如果 LLM 不可用则回退到静态消息。

    参数:
        user_message: 原始用户消息。
        brand: 缺少菜单覆盖的品牌名称。
        query: 饮品类别查询。
        max_price: 可选的预算约束。

    返回:
        元组 (reply_text, usage_tokens)，其中 usage_tokens 是
        (input_tokens, output_tokens)，如果未使用 LLM 则为 None。
    """
    try:
        from openai import AsyncOpenAI
    except Exception:
        query_text = query or "饮品"
        budget_text = f"，预算尽量控制在 {max_price} 元以内" if max_price is not None else ""
        return f"{brand} 这边我暂时没有现成菜单；如果你今天想喝 {query_text}{budget_text}，可以先挑一个你平时更喜欢的口味方向。", None

    api_key = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    if not api_key:
        query_text = query or "饮品"
        budget_text = f"，预算尽量控制在 {max_price} 元以内" if max_price is not None else ""
        return f"{brand} 这边我暂时没有现成菜单；如果你今天想喝 {query_text}{budget_text}，可以先挑一个你平时更喜欢的口味方向。", None

    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=20)
    system_prompt = "不要声称自己查到了该品牌真实菜单。"
    response = await client.chat.completions.create(
        model=_menu_generation_model(),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0.5,
        extra_body={"enable_thinking": False},
    )
    text = _extract_generated_text(response.choices[0].message.content if response.choices else "").strip()
    if text:
        return text, extract_usage_tokens(response)
    query_text = query or "饮品"
    budget_text = f"，预算尽量控制在 {max_price} 元以内" if max_price is not None else ""
    return f"{brand} 这边我暂时没有现成菜单；如果你今天想喝 {query_text}{budget_text}，可以先挑一个你平时更喜欢的口味方向。", extract_usage_tokens(response)


def _is_simple_menu_request(message: str) -> bool:
    """检查消息是否为简单的菜单推荐请求。

    简单请求包含动作动词 + 品牌 + 类别，可使用快速路径。

    参数:
        message: 要分析的用户消息。

    返回:
        如果消息符合菜单快速路径处理条件则返回 True。
    """
    return any(token in message for token in ("推荐", "喝什么", "想喝", "找", "有什么", "有啥", "哪款", "热门", "人气", "top")) and _extract_brand(message) is not None and _extract_menu_query(message) is not None


def _detect_stats_period(message: str) -> str | None:
    """从用户消息中检测统计时间段。

    识别中文时间表达式，如这周、这个月、累计。

    参数:
        message: 要解析的用户消息。

    返回:
        时间段标识符（'week'、'month'、'all'），如果未检测到则返回 None。
    """
    if any(token in message for token in ("这周", "本周")):
        return "week"
    if any(token in message for token in ("这个月", "本月")):
        return "month"
    if any(token in message for token in ("一共", "总共", "累计", "至今")):
        return "all"
    return None


def _is_simple_stats_request(message: str) -> bool:
    """检查消息是否为简单的统计查询。

    简单统计请求包含时间段 + 消费指标关键词。

    参数:
        message: 要分析的用户消息。

    返回:
        如果消息符合统计快速路径处理条件则返回 True。
    """
    return _detect_stats_period(message) is not None and any(token in message for token in ("多少杯", "几杯", "喝了几次", "花了多少", "花多少钱", "总花费", "消费"))


def _render_stats_reply(period: str, stats: dict[str, Any]) -> str:
    """渲染用户友好的统计摘要回复。

    格式化饮品数量、总消费、平均价格和热门品牌。

    参数:
        period: 时间段标识符（'week'、'month'、'all'）。
        stats: 包含 'total_count'、'total_amount'、'brand_dist' 的统计字典。

    返回:
        格式化的饮品统计中文摘要文本。
    """
    period_label = {"week": "这周", "month": "这个月", "all": "累计"}.get(period, "当前")
    total_count = int(stats.get("total_count") or 0)
    total_amount = float(stats.get("total_amount") or 0)
    if total_count <= 0:
        return f"{period_label}还没有饮品记录。"
    avg = round(total_amount / total_count, 2) if total_count else 0
    lines = [f"{period_label}你一共喝了 {total_count} 杯，花了 {total_amount:.1f} 元，平均每杯 {avg:.2f} 元。"]
    brands = list(stats.get("brand_dist") or [])
    if brands:
        top = brands[:3]
        lines.append("喝得最多的是：" + "、".join(f"{item['brand']} {item['count']} 杯" for item in top))
    return "\n".join(lines)


def _is_recent_records_request(message: str) -> bool:
    """检查消息是否为最近记录查询。

    检测询问最近饮品历史的短语。

    参数:
        message: 要分析的用户消息。

    返回:
        如果消息询问最近饮品记录则返回 True。
    """
    return any(token in message for token in ("最近喝了什么", "最近喝了啥", "我上次喝了什么", "最近几杯", "最近的记录", "最近一杯", "最近一次喝了什么"))


def _recent_records_limit_for_message(message: str) -> int:
    """根据消息具体程度确定记录数量限制。

    单条记录查询限制为 1，一般查询限制为 5。

    参数:
        message: 要分析的用户消息。

    返回:
        要检索的记录数量。
    """
    if any(token in message for token in ("我上次喝了什么", "最近一杯")):
        return 1
    return 5


def _render_recent_records_reply(records: list[dict[str, Any]]) -> str:
    """渲染用户友好的最近记录摘要回复。

    格式化单条记录或最近饮品记录列表及详情。

    参数:
        records: 饮品记录字典列表。

    返回:
        描述最近饮品的格式化中文文本。
    """
    if not records:
        return "最近还没有饮品记录。"
    if len(records) == 1:
        item = records[0]
        consumed_at = str(item.get("consumed_at") or "")[:10]
        price = item.get("price")
        price_text = f"，¥{price}" if price is not None else ""
        return f"你最近一杯是 {consumed_at} 的 {item.get('brand')} {item.get('name')}{price_text}。"
    lines = ["最近几条记录："]
    for item in records[:5]:
        consumed_at = str(item.get("consumed_at") or "")[:10]
        price = item.get("price")
        price_text = f" · ¥{price}" if price is not None else ""
        lines.append(f"- {consumed_at} {item.get('brand')} {item.get('name')}{price_text}")
    return "\n".join(lines)


def _detect_day_anchor(message: str) -> tuple[str, str] | None:
    """从用户消息中检测特定日期引用。

    识别 "今天" 和 "昨天" 锚点。

    参数:
        message: 要解析的用户消息。

    返回:
        元组 (day_label, iso_date)，如果未找到日期锚点则返回 None。
    """
    today = datetime.now().date()
    if "今天" in message:
        return ("今天", today.isoformat())
    if "昨天" in message:
        return ("昨天", (today - timedelta(days=1)).isoformat())
    return None


def _is_day_records_request(message: str) -> bool:
    """检查消息是否为特定日期记录查询。

    需要日期锚点 + 记录/消费关键词。

    参数:
        message: 要分析的用户消息。

    返回:
        如果消息询问特定日期的饮品记录则返回 True。
    """
    if _detect_day_anchor(message) is None:
        return False
    return any(token in message for token in ("喝了什么", "喝了啥", "喝的什么", "几杯", "多少杯", "花了多少", "花多少钱", "消费", "记录"))


def _render_day_records_reply(day_label: str, payload: dict[str, Any], message: str) -> str:
    """渲染用户友好的日期记录摘要回复。

    格式化特定日期的记录，根据问题类型
    （消费、数量或项目详情）调整。

    参数:
        day_label: 中文日期标签（如 "今天"、"昨天"）。
        payload: 包含该日期 'records' 列表的字典。
        message: 用于确定回复重点的原始消息。

    返回:
        描述当天饮品的格式化中文文本。
    """
    records = list(payload.get("records") or [])
    total_amount = sum(float(item.get("price") or 0) for item in records)
    if not records:
        return f"{day_label}还没有饮品记录。"

    if any(token in message for token in ("花了多少", "消费", "花费")):
        return f"{day_label}你花了 {total_amount:.1f} 元，一共喝了 {len(records)} 杯。"

    if any(token in message for token in ("几杯", "多少杯")):
        return f"{day_label}你一共喝了 {len(records)} 杯。"

    if len(records) == 1:
        item = records[0]
        price = item.get("price")
        price_text = f"，¥{price}" if price is not None else ""
        return f"{day_label}你喝了 {item.get('brand')} {item.get('name')}{price_text}。"

    lines = [f"{day_label}你喝了 {len(records)} 杯："]
    for item in records[:5]:
        price = item.get("price")
        price_text = f" · ¥{price}" if price is not None else ""
        lines.append(f"- {item.get('brand')} {item.get('name')}{price_text}")
    if total_amount:
        lines.append(f"合计 {total_amount:.1f} 元。")
    return "\n".join(lines)


def _filter_menu_results(results: list[dict[str, Any]], *, max_price: int | None) -> list[dict[str, Any]]:
    """按价格和相关性过滤并排序菜单结果。

    移除加料项目，应用价格上限，按分数（降序）、
    价格（升序）、名称排序。

    参数:
        results: 菜单项字典列表。
        max_price: 可选的最高价格过滤（人民币）。

    返回:
        过滤并排序后的菜单项列表。
    """
    filtered = results
    if max_price is not None:
        filtered = [item for item in filtered if isinstance(item.get("price"), (int, float)) and float(item["price"]) <= max_price]
    # Filter out obvious add-ons/toppings for recommendation-style replies.
    filtered = [
        item
        for item in filtered
        if not any(token in str(item.get("name") or "") for token in ("果粒", "加料", "小料"))
    ]
    filtered.sort(key=lambda item: (-float(item.get("score") or 0), float(item.get("price") or 9999), str(item.get("name") or "")))
    return filtered


def _render_fast_menu_reply(*, brand: str, query: str, max_price: int | None, results: list[dict[str, Any]], message: str) -> str:
    """渲染用户友好的菜单推荐回复。

    格式化前 3 个菜单项的名称、价格和描述。

    参数:
        brand: 推荐的品牌名称。
        query: 正在推荐的饮品类别。
        max_price: 已应用的可选预算约束。
        results: 过滤后的菜单项字典列表。
        message: 原始用户消息（未使用，用于未来个性化）。

    返回:
        包含菜单推荐的格式化中文文本。
    """
    if not results:
        budget_text = f"{max_price} 元以内" if max_price is not None else "当前条件下"
        return f"没找到 {brand}{budget_text} 的{query}推荐。可以放宽预算，或者我帮你换个品牌继续找。"

    budget_text = f"{max_price} 元以内" if max_price is not None else ""
    lines = [f"{brand}{budget_text}可以先看这几款{query}："]
    for idx, item in enumerate(results[:3], start=1):
        price = item.get("price")
        price_text = f"¥{int(price) if isinstance(price, (int, float)) and float(price).is_integer() else price}" if price is not None else "价格待确认"
        description = str(item.get("description") or "").strip()
        lines.append(f"{idx}. {item.get('name')}（{price_text}）")
        if description:
            lines.append(f"- {description}")
    return "\n".join(lines)


def _render_menu_coverage_gap_reply(*, brand: str, query: str | None, fallback_results: list[dict[str, Any]]) -> str:
    """当品牌缺少菜单覆盖时渲染回复。

    解释覆盖缺口并可选显示跨品牌替代方案。

    参数:
        brand: 缺少覆盖的品牌名称。
        query: 已搜索的饮品类别。
        fallback_results: 跨品牌搜索结果（如有）。

    返回:
        解释缺口并建议替代方案的格式化中文文本。
    """
    query_text = query or "饮品"
    if not fallback_results:
        return f"我这边的菜单库暂时还没收录 {brand}，所以没法给你做 {brand} 内的{query_text}推荐。你可以告诉我别的品牌，或者我帮你按同类型口味跨品牌推荐。"

    lines = [f"我这边的菜单库暂时还没收录 {brand}，先给你找了几款相近的{query_text}作参考："]
    for item in fallback_results[:3]:
        price = item.get("price")
        price_text = f"¥{int(price) if isinstance(price, (int, float)) and float(price).is_integer() else price}" if price is not None else "价格待确认"
        lines.append(f"- {item.get('brand')} {item.get('name')} · {price_text}")
    lines.append(f"如果你希望严格限定在 {brand}，我建议先补齐这个品牌菜单再做品牌内推荐。")
    return "\n".join(lines)


async def _run_memory_jobs_after_response(user_id: str, thread_id: str) -> None:
    """在聊天响应后将后台记忆处理任务加入队列。

    调度线程摘要刷新、记忆提取和用户画像刷新。

    参数:
        user_id: 记忆任务的用户标识符。
        thread_id: 会话线程标识符。
    """
    await asyncio.gather(
        asyncio.to_thread(enqueue_memory_job, user_id, "thread_summary_refresh", {"thread_key": thread_id}, thread_id),
        asyncio.to_thread(enqueue_memory_job, user_id, "memory_extract_from_thread", {"thread_key": thread_id}, thread_id),
        asyncio.to_thread(enqueue_memory_job, user_id, "profile_refresh_from_records", {}, thread_id),
    )


async def _daily_budget_snapshot_async(*, user_id: str, model: str) -> dict[str, Any]:
    return await asyncio.to_thread(_daily_budget_snapshot, user_id=user_id, model=model)


async def _create_thread_async(user_id: str, thread_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(repository.create_thread, user_id, thread_id)


async def _append_message_async(**kwargs: Any) -> dict[str, Any]:
    return await asyncio.to_thread(repository.append_message, **kwargs)


async def _record_usage_async(**kwargs: Any) -> dict[str, Any] | None:
    return await asyncio.to_thread(record_usage, **kwargs)


async def _get_stats_async(**kwargs: Any) -> dict[str, Any]:
    return await asyncio.to_thread(get_stats_impl, **kwargs)


async def _get_recent_records_async(**kwargs: Any) -> dict[str, Any]:
    return await asyncio.to_thread(get_recent_records_impl, **kwargs)


async def _get_day_async(**kwargs: Any) -> dict[str, Any]:
    return await asyncio.to_thread(get_day_impl, **kwargs)


def _format_sse(payload: dict[str, Any], *, event: str | None = None, event_id: str | None = None) -> str:
    """将字典格式化为 Server-Sent Events (SSE) 消息。

    参数:
        payload: 要作为 JSON 发送的数据字典。
        event: 可选的 SSE 事件类型名称。
        event_id: 可选的 SSE 事件 ID。

    返回:
        包含 event、id 和 data 字段的格式化 SSE 字符串。
    """
    lines: list[str] = []
    if event:
        lines.append(f"event: {event}")
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False, default=str)}")
    return "\n".join(lines) + "\n\n"


def _extract_text(chunk: Any) -> str:
    """从各种流块格式中提取文本内容。

    处理来自 LLM 流的字符串、字典、列表和对象格式。

    参数:
        chunk: 各种格式的流块。

    返回:
        提取的文本字符串，如果未找到文本则返回空字符串。
    """
    content = getattr(chunk, "content", chunk)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    out.append(str(part.get("text", "")))
                elif part.get("type") == "output_text":
                    out.append(str(part.get("text", "")))
            else:
                text = getattr(part, "text", "")
                if text:
                    out.append(str(text))
        return "".join(out)
    return str(content)


def _session_thread_id(user_id: str, session_id: str) -> str:
    """将会话 ID 规范化为标准线程 ID 格式。

    将各种会话 ID 格式转换为 'user-{user_id}:session-{session_id}'。

    参数:
        user_id: 用户标识符。
        session_id: 来自客户端的原始会话 ID。

    返回:
        'user-xxx:session-yyy' 格式的标准线程 ID。
    """
    return normalize_session_thread_id(user_id, session_id)


@router.post("/chat")
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    """处理 Agent 聊天请求，返回流式 SSE 响应。

    该端点通过 Bobo 饮品助手 Agent 处理用户消息，
    支持多种执行模式：

    - **菜单快速路径**: 针对简单品牌+类别查询的直接菜单搜索
    - **统计快速路径**: 时间段查询的快速统计检索
    - **最近记录快速路径**: 最近饮品历史查询
    - **日期记录快速路径**: 特定日期记录查询
    - **Agent 图**: 复杂查询的完整 LangGraph Agent

    该端点强制执行每日 LLM 预算限制，并通过 Server-Sent Events (SSE)
    流式传输响应，事件类型包括：meta、tool_call、tool_result、text、error、done。

    参数:
        payload: 包含 message、thread_id 和可选参数的 ChatRequest。
        request: 在 state 中包含已认证 user_id 的 FastAPI 请求。

    返回:
        包含 SSE 事件的 StreamingResponse，用于实时 Agent 输出。

    异常:
        HTTPException: 401 如果用户未认证。
        HTTPException: 429 如果每日 LLM 预算已耗尽或不足。

    示例:
        POST /bobo/agent/chat
        {
            "message": "推荐一杯霸王茶姬的果茶，20元以内",
            "thread_id": "session-abc123",
            "max_steps": 10
        }
    """
    request_user_id = getattr(request.state, "user_id", None)
    if not request_user_id:
        raise HTTPException(status_code=401, detail="missing authenticated user")
    user_id = request_user_id
    client_ip = getattr(getattr(request, "client", None), "host", "unknown")
    enforce_rate_limit(scope="agent:chat:user", key=f"{user_id}:{client_ip}", max_requests=30, window_seconds=60)
    request_id = (
        getattr(request.state, "request_id", None)
        or request.headers.get("X-Request-ID")
        or request.headers.get("X-Request-Id")
        or uuid4().hex
    )
    thread_id = _session_thread_id(user_id, payload.thread_id)
    request_start = time.perf_counter()
    chat_model = _chat_model()
    budget_snapshot = await _daily_budget_snapshot_async(user_id=user_id, model=chat_model)
    reserve = _message_budget_reserve(user_message=payload.message, chat_model=chat_model)
    available_output_tokens = _available_output_tokens_after_reserve(snapshot=budget_snapshot, reserve=reserve)
    if budget_snapshot["remaining_cny"] <= 0:
        observe_agent_budget_check(
            outcome="budget_exhausted",
            remaining_cny=budget_snapshot["remaining_cny"],
            reserved_cost_cny=reserve["reserved_cost_cny"],
            available_output_tokens=available_output_tokens,
        )
        raise HTTPException(status_code=429, detail=_budget_error_detail(snapshot=budget_snapshot))
    if budget_snapshot["remaining_cny"] <= reserve["reserved_cost_cny"] or available_output_tokens <= 0:
        observe_agent_budget_check(
            outcome="reserve_blocked",
            remaining_cny=budget_snapshot["remaining_cny"],
            reserved_cost_cny=reserve["reserved_cost_cny"],
            available_output_tokens=available_output_tokens,
        )
        raise HTTPException(
            status_code=429,
            detail=_budget_reserve_error_detail(
                snapshot=budget_snapshot,
                reserve=reserve,
                available_output_tokens=available_output_tokens,
            ),
        )
    observe_agent_budget_check(
        outcome="allowed",
        remaining_cny=budget_snapshot["remaining_cny"],
        reserved_cost_cny=reserve["reserved_cost_cny"],
        available_output_tokens=available_output_tokens,
    )
    if payload.user_id and payload.user_id != request_user_id:
        logger.warning(
            "chat request ignored body user_id override request_user_id=%s body_user_id=%s request_id=%s",
            request_user_id,
            payload.user_id,
            request_id,
        )
    audit_agent_event(
        "chat",
        stage="start",
        user_id=user_id,
        thread_id=thread_id,
        request_id=request_id,
        message_len=len(payload.message),
    )
    await _create_thread_async(user_id, thread_id)
    await _append_message_async(
        user_id=user_id,
        thread_key=thread_id,
        role="user",
        content=payload.message,
        request_id=request_id,
        source="agent",
    )
    _log_phase(request_id, "request_started", thread_id=thread_id, message=payload.message[:120], max_steps=payload.max_steps)

    async def _event_stream():
        assistant_chunks: list[str] = []
        assistant_output_tokens_estimate = 0
        first_text_sent = False
        active_mode = "agent_graph"
        usage_prompt_tokens: int | None = None
        usage_completion_tokens: int | None = None
        tool_start_times: dict[str, float] = {}

        def _record_first_token(mode: str) -> None:
            nonlocal first_text_sent
            if first_text_sent:
                return
            first_text_sent = True
            observe_agent_first_token(mode=mode, duration_seconds=time.perf_counter() - request_start)
            _log_phase(
                request_id,
                "first_text_chunk",
                elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
            )

        try:
            yield _format_sse(
                {
                    "type": "meta",
                        "request_id": request_id,
                        "thread_id": thread_id,
                        "user_id": user_id,
                        "max_steps": payload.max_steps,
                        "remaining_output_tokens": available_output_tokens,
                    },
                event="meta",
                event_id=request_id,
            )
            _log_phase(request_id, "meta_sent", elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2))

            if _is_simple_menu_request(payload.message):
                brand = _extract_brand(payload.message)
                query = _extract_menu_query(payload.message)
                max_price = _extract_budget_ceiling(payload.message)
                if brand and query:
                    active_mode = "menu_fast_path"
                    observe_fast_path(path=active_mode, outcome="selected")
                    _log_phase(
                        request_id,
                        "fast_path_selected",
                        elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                        brand=brand,
                        query=query,
                        max_price=max_price,
                    )
                    yield _format_sse(
                        {
                            "type": "tool_call",
                            "tool": "search_menu",
                            "args": {"brand": brand, "query": query, "thread_id": thread_id, "strategy": "multi_query_recall"},
                            "request_id": request_id,
                        },
                        event="tool_call",
                        event_id=request_id,
                    )
                    tool_start = time.perf_counter()
                    raw_result = await _retrieve_menu_fast_path(
                        message=payload.message,
                        brand=brand,
                        query=query,
                        max_price=max_price,
                        user_id=user_id,
                        request_id=request_id,
                        thread_id=thread_id,
                    )
                    _log_phase(
                        request_id,
                        "fast_path_tool_result",
                        elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                        tool_duration_ms=round((time.perf_counter() - tool_start) * 1000, 2),
                        result_count=len(list(raw_result.get("results") or [])),
                        attempted_queries=raw_result.get("attempted_queries"),
                        brand_has_coverage=raw_result.get("brand_has_coverage"),
                    )
                    yield _format_sse(
                        {
                            "type": "tool_result",
                            "tool": "search_menu",
                            "output": raw_result,
                            "request_id": request_id,
                        },
                        event="tool_result",
                        event_id=request_id,
                    )
                    await _append_message_async(
                        user_id=user_id,
                        thread_key=thread_id,
                        role="tool",
                        content=str(raw_result),
                        request_id=request_id,
                        tool_name="search_menu",
                        source="agent_fast_path",
                    )
                    final_results = list(raw_result.get("results") or [])
                    llm_usage_tokens: tuple[int, int] | None = None
                    if final_results:
                        reply_text = _render_fast_menu_reply(
                            brand=brand,
                            query=query,
                            max_price=max_price,
                            results=final_results,
                            message=payload.message,
                        )
                    elif not raw_result.get("brand_has_coverage"):
                        reply_text, llm_usage_tokens = await _generate_unstructured_menu_reply(
                            user_message=payload.message,
                            brand=brand,
                            query=query,
                            max_price=max_price,
                        )
                    else:
                        reply_text = _render_fast_menu_reply(
                            brand=brand,
                            query=query,
                            max_price=max_price,
                            results=final_results,
                            message=payload.message,
                        )
                    yield _format_sse(
                        {"type": "text", "content": reply_text, "request_id": request_id},
                        event="text",
                        event_id=request_id,
                    )
                    _record_first_token(active_mode)
                    await _append_message_async(
                        user_id=user_id,
                        thread_key=thread_id,
                        role="assistant",
                        content=reply_text,
                        request_id=request_id,
                        source="agent_fast_path",
                    )
                    if llm_usage_tokens:
                        await _record_usage_async(
                            user_id=user_id,
                            model=_chat_model(),
                            input_tokens=llm_usage_tokens[0],
                            output_tokens=llm_usage_tokens[1],
                            usage_kind="menu_gap_generation",
                        )
                    await _run_memory_jobs_after_response(user_id, thread_id)
                    yield _format_sse({"type": "done", "request_id": request_id}, event="done", event_id=request_id)
                    _log_phase(
                        request_id,
                        "fast_path_done",
                        elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                    )
                    audit_agent_event(
                        "chat",
                        stage="success",
                        user_id=user_id,
                        thread_id=thread_id,
                        request_id=request_id,
                        mode="fast_path",
                    )
                    observe_agent_chat(mode=active_mode, outcome="success", duration_seconds=time.perf_counter() - request_start)
                    observe_fast_path(path=active_mode, outcome="success")
                    observe_task_execution(task=active_mode, outcome="success", source="agent")
                    return

            if _is_simple_stats_request(payload.message):
                active_mode = "stats_fast_path"
                observe_fast_path(path=active_mode, outcome="selected")
                period = _detect_stats_period(payload.message) or "all"
                _log_phase(
                    request_id,
                    "stats_fast_path_selected",
                    elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                    period=period,
                )
                stats = await _get_stats_async(
                    period=period,
                    date=None,
                    user_id=user_id,
                    request_id=request_id,
                    thread_id=thread_id,
                    source="agent_fast_path",
                )
                reply_text = _render_stats_reply(period, stats)
                yield _format_sse(
                    {"type": "text", "content": reply_text, "request_id": request_id},
                    event="text",
                    event_id=request_id,
                )
                _record_first_token(active_mode)
                await _append_message_async(
                    user_id=user_id,
                    thread_key=thread_id,
                    role="assistant",
                    content=reply_text,
                    request_id=request_id,
                    source="agent_fast_path",
                )
                await _run_memory_jobs_after_response(user_id, thread_id)
                yield _format_sse({"type": "done", "request_id": request_id}, event="done", event_id=request_id)
                _log_phase(
                    request_id,
                    "stats_fast_path_done",
                    elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                    total_count=stats.get("total_count"),
                )
                audit_agent_event(
                    "chat",
                    stage="success",
                    user_id=user_id,
                    thread_id=thread_id,
                    request_id=request_id,
                    mode="stats_fast_path",
                )
                observe_agent_chat(mode=active_mode, outcome="success", duration_seconds=time.perf_counter() - request_start)
                observe_fast_path(path=active_mode, outcome="success")
                observe_task_execution(task=active_mode, outcome="success", source="agent")
                return

            if _is_recent_records_request(payload.message):
                active_mode = "recent_records_fast_path"
                observe_fast_path(path=active_mode, outcome="selected")
                recent_limit = _recent_records_limit_for_message(payload.message)
                _log_phase(
                    request_id,
                    "recent_records_fast_path_selected",
                    elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                    limit=recent_limit,
                )
                payload_recent = await _get_recent_records_async(
                    limit=recent_limit,
                    user_id=user_id,
                    request_id=request_id,
                    thread_id=thread_id,
                    source="agent_fast_path",
                )
                records = list(payload_recent.get("records") or [])
                reply_text = _render_recent_records_reply(records)
                yield _format_sse(
                    {"type": "text", "content": reply_text, "request_id": request_id},
                    event="text",
                    event_id=request_id,
                )
                _record_first_token(active_mode)
                await _append_message_async(
                    user_id=user_id,
                    thread_key=thread_id,
                    role="assistant",
                    content=reply_text,
                    request_id=request_id,
                    source="agent_fast_path",
                )
                await _run_memory_jobs_after_response(user_id, thread_id)
                yield _format_sse({"type": "done", "request_id": request_id}, event="done", event_id=request_id)
                _log_phase(
                    request_id,
                    "recent_records_fast_path_done",
                    elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                    result_count=len(records),
                )
                audit_agent_event(
                    "chat",
                    stage="success",
                    user_id=user_id,
                    thread_id=thread_id,
                    request_id=request_id,
                    mode="recent_records_fast_path",
                )
                observe_agent_chat(mode=active_mode, outcome="success", duration_seconds=time.perf_counter() - request_start)
                observe_fast_path(path=active_mode, outcome="success")
                observe_task_execution(task=active_mode, outcome="success", source="agent")
                return

            if _is_day_records_request(payload.message):
                day_anchor = _detect_day_anchor(payload.message)
                if day_anchor:
                    active_mode = "day_records_fast_path"
                    observe_fast_path(path=active_mode, outcome="selected")
                    day_label, target_date = day_anchor
                    _log_phase(
                        request_id,
                        "day_records_fast_path_selected",
                        elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                        date=target_date,
                    )
                    payload_day = await _get_day_async(
                        date=target_date,
                        user_id=user_id,
                        request_id=request_id,
                        thread_id=thread_id,
                        source="agent_fast_path",
                    )
                    reply_text = _render_day_records_reply(day_label, payload_day, payload.message)
                    yield _format_sse(
                        {"type": "text", "content": reply_text, "request_id": request_id},
                        event="text",
                        event_id=request_id,
                    )
                    _record_first_token(active_mode)
                    await _append_message_async(
                        user_id=user_id,
                        thread_key=thread_id,
                        role="assistant",
                        content=reply_text,
                        request_id=request_id,
                        source="agent_fast_path",
                    )
                    await _run_memory_jobs_after_response(user_id, thread_id)
                    yield _format_sse({"type": "done", "request_id": request_id}, event="done", event_id=request_id)
                    _log_phase(
                        request_id,
                        "day_records_fast_path_done",
                        elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                        record_count=len(list(payload_day.get("records") or [])),
                    )
                    audit_agent_event(
                        "chat",
                        stage="success",
                        user_id=user_id,
                        thread_id=thread_id,
                        request_id=request_id,
                        mode="day_records_fast_path",
                    )
                    observe_agent_chat(mode=active_mode, outcome="success", duration_seconds=time.perf_counter() - request_start)
                    observe_fast_path(path=active_mode, outcome="success")
                    observe_task_execution(task=active_mode, outcome="success", source="agent")
                    return

            observe_fast_path(path="agent_graph", outcome="fallback")

            async for event in stream_agent_events(
                message=payload.message,
                user_id=user_id,
                thread_id=thread_id,
                max_steps=payload.max_steps,
                request_id=request_id,
            ):
                event_name = event.get("event")
                if event_name == "on_chat_model_stream":
                    text = _extract_text(event.get("data", {}).get("chunk"))
                    if text:
                        assistant_output_tokens_estimate += estimate_tokens(text)
                        if assistant_output_tokens_estimate > available_output_tokens:
                            limit_note = "\n\n今天的 AI 额度已经用完了，先到这里。明天再继续聊。"
                            assistant_chunks.append(limit_note)
                            _record_first_token(active_mode)
                            yield _format_sse(
                                {"type": "text", "content": limit_note, "request_id": request_id},
                                event="text",
                                event_id=request_id,
                            )
                            break
                        if not first_text_sent:
                            _record_first_token(active_mode)
                        assistant_chunks.append(text)
                        yield _format_sse(
                            {"type": "text", "content": text, "request_id": request_id},
                            event="text",
                            event_id=request_id,
                        )
                elif event_name == "on_chat_model_end":
                    usage = extract_usage_tokens(event.get("data", {}))
                    if usage:
                        usage_prompt_tokens, usage_completion_tokens = usage
                elif event_name == "on_tool_start":
                    tool_name = str(event.get("name", "") or "unknown")
                    tool_run_id = str(event.get("run_id", "") or tool_name)
                    tool_start_times[tool_run_id] = time.perf_counter()
                    _log_phase(
                        request_id,
                        "tool_start",
                        elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                        tool=tool_name,
                    )
                    yield _format_sse(
                        {
                            "type": "tool_call",
                            "tool": tool_name,
                            "args": event.get("data", {}).get("input", {}),
                            "request_id": request_id,
                        },
                        event="tool_call",
                        event_id=request_id,
                    )
                elif event_name == "on_tool_end":
                    tool_name = str(event.get("name", "") or "unknown")
                    tool_run_id = str(event.get("run_id", "") or tool_name)
                    tool_duration = None
                    if tool_run_id in tool_start_times:
                        tool_duration = time.perf_counter() - tool_start_times.pop(tool_run_id)
                    output = event.get("data", {}).get("output")
                    rendered_output = _extract_text(output) or output
                    result_count = None
                    if isinstance(rendered_output, dict):
                        result_count = len(list(rendered_output.get("results") or []))
                    _log_phase(
                        request_id,
                        "tool_end",
                        elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                        tool=tool_name,
                        result_count=result_count,
                    )
                    observe_agent_tool_call(tool=tool_name, outcome="success", duration_seconds=tool_duration)
                    yield _format_sse(
                        {
                            "type": "tool_result",
                            "tool": tool_name,
                            "output": rendered_output,
                            "request_id": request_id,
                        },
                        event="tool_result",
                        event_id=request_id,
                    )
                    await _append_message_async(
                        user_id=user_id,
                        thread_key=thread_id,
                        role="tool",
                        content=str(rendered_output),
                        request_id=request_id,
                        tool_name=tool_name,
                        tool_call_id=str(event.get("run_id", "") or ""),
                        source="agent",
                    )
            assistant_text = "".join(assistant_chunks).strip()
            if assistant_text:
                await _append_message_async(
                    user_id=user_id,
                    thread_key=thread_id,
                    role="assistant",
                    content=assistant_text,
                    request_id=request_id,
                    source="agent",
                )
                prompt_tokens = usage_prompt_tokens if usage_prompt_tokens is not None else estimate_tokens(payload.message)
                completion_tokens = (
                    usage_completion_tokens if usage_completion_tokens is not None else assistant_output_tokens_estimate or estimate_tokens(assistant_text)
                )
                await _record_usage_async(
                    user_id=user_id,
                    model=chat_model,
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    usage_kind="chat_main",
                )
            await _run_memory_jobs_after_response(user_id, thread_id)
            yield _format_sse({"type": "done", "request_id": request_id}, event="done", event_id=request_id)
            _log_phase(
                request_id,
                "chat_done",
                elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                assistant_chars=len(assistant_text),
            )
            audit_agent_event(
                "chat",
                stage="success",
                user_id=user_id,
                thread_id=thread_id,
                request_id=request_id,
                mode="agent_graph",
            )
            observe_agent_chat(mode=active_mode, outcome="success", duration_seconds=time.perf_counter() - request_start)
        except RuntimeError as exc:
            if assistant_chunks:
                await _append_message_async(
                    user_id=user_id,
                    thread_key=thread_id,
                    role="assistant",
                    content="".join(assistant_chunks).strip(),
                    request_id=request_id,
                    source="agent",
                )
            yield _format_sse(
                {"type": "error", "error": str(exc), "request_id": request_id},
                event="error",
                event_id=request_id,
            )
            yield _format_sse({"type": "done", "request_id": request_id}, event="done", event_id=request_id)
            audit_agent_event(
                "chat",
                stage="error",
                user_id=user_id,
                thread_id=thread_id,
                request_id=request_id,
                error=str(exc),
            )
            observe_agent_chat(mode=active_mode, outcome="runtime_error", duration_seconds=time.perf_counter() - request_start)
        except Exception as exc:
            if assistant_chunks:
                await _append_message_async(
                    user_id=user_id,
                    thread_key=thread_id,
                    role="assistant",
                    content="".join(assistant_chunks).strip(),
                    request_id=request_id,
                    source="agent",
                )
            yield _format_sse(
                {"type": "error", "error": str(exc), "request_id": request_id},
                event="error",
                event_id=request_id,
            )
            yield _format_sse({"type": "done", "request_id": request_id}, event="done", event_id=request_id)
            audit_agent_event(
                "chat",
                stage="error",
                user_id=user_id,
                thread_id=thread_id,
                request_id=request_id,
                error=str(exc),
            )
            observe_agent_chat(mode=active_mode, outcome="error", duration_seconds=time.perf_counter() - request_start)

    return StreamingResponse(_event_stream(), media_type="text/event-stream")
