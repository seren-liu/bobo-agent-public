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
from app.memory.jobs import enqueue_memory_job, schedule_memory_jobs
from app.memory import repository
from app.observability import observe_agent_budget_check, observe_agent_chat, observe_agent_first_token, observe_agent_tool_call
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
    message: str = Field(min_length=1)
    thread_id: str
    user_id: str | None = Field(default=None, description="Deprecated legacy fallback; request.state.user_id takes precedence.")
    max_steps: int = Field(default=10, ge=1, le=30)


def _log_phase(request_id: str, phase: str, **fields: Any) -> None:
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
    normalized = re.sub(r"\s+", "", message)
    for token in ("给我", "推荐", "一下", "一杯", "喝什么", "想喝", "给我来", "帮我"):
        normalized = normalized.replace(token, "")
    if brand:
        normalized = normalized.replace(brand, "")
    return normalized.strip("的呀吧呢吗，。！ ")


def _menu_category_aliases(query: str | None) -> list[str]:
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
    brand_coverage = get_menu_brand_coverage_impl(
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
    return os.getenv("QWEN_CHAT_MODEL") or "qwen3-32b"


def _chat_model() -> str:
    return os.getenv("QWEN_CHAT_MODEL") or "qwen3-32b"


def _daily_budget_snapshot(*, user_id: str, model: str) -> dict[str, Any]:
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
    available_cost_cny = max(snapshot["remaining_cny"] - reserve["reserved_cost_cny"], 0.0)
    return affordable_output_tokens(pricing=snapshot["pricing"], available_cost_cny=available_cost_cny)


def _budget_error_detail(*, snapshot: dict[str, Any]) -> str:
    pricing = snapshot["pricing"]
    spent = snapshot["spent_cost_cny"]
    budget = snapshot["budget_cny"]
    remaining_output_tokens = snapshot["remaining_output_tokens"]
    return (
        f"今日 AI 问答预算已用完。当前模型 {pricing.model} 按官方非思考价估算，"
        f"今日已用约 {spent:.3f} / {budget:.2f} 元；剩余可用输出 token 约 {remaining_output_tokens}。"
    )


def _budget_reserve_error_detail(*, snapshot: dict[str, Any], reserve: dict[str, Any], available_output_tokens: int) -> str:
    pricing = snapshot["pricing"]
    return (
        f"今日 AI 问答预算不足以开启新一轮对话。当前模型 {pricing.model} 按官方非思考价估算，"
        f"今日已用约 {snapshot['spent_cost_cny']:.3f} / {snapshot['budget_cny']:.2f} 元；"
        f"本轮请求预留成本约 {reserve['reserved_cost_cny']:.4f} 元，剩余可用输出 token 约 {available_output_tokens}。"
    )


def _extract_generated_text(raw: Any) -> str:
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
    return any(token in message for token in ("推荐", "喝什么", "想喝", "找")) and _extract_brand(message) is not None and _extract_menu_query(message) is not None


def _detect_stats_period(message: str) -> str | None:
    if any(token in message for token in ("这周", "本周")):
        return "week"
    if any(token in message for token in ("这个月", "本月")):
        return "month"
    if any(token in message for token in ("一共", "总共", "累计", "至今")):
        return "all"
    return None


def _is_simple_stats_request(message: str) -> bool:
    return _detect_stats_period(message) is not None and any(token in message for token in ("多少杯", "几杯", "花了多少", "总花费", "消费"))


def _render_stats_reply(period: str, stats: dict[str, Any]) -> str:
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
    return any(token in message for token in ("最近喝了什么", "最近喝了啥", "我上次喝了什么", "最近几杯", "最近的记录", "最近一杯"))


def _recent_records_limit_for_message(message: str) -> int:
    if any(token in message for token in ("我上次喝了什么", "最近一杯")):
        return 1
    return 5


def _render_recent_records_reply(records: list[dict[str, Any]]) -> str:
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
    today = datetime.now().date()
    if "今天" in message:
        return ("今天", today.isoformat())
    if "昨天" in message:
        return ("昨天", (today - timedelta(days=1)).isoformat())
    return None


def _is_day_records_request(message: str) -> bool:
    if _detect_day_anchor(message) is None:
        return False
    return any(token in message for token in ("喝了什么", "喝了啥", "喝的什么", "几杯", "多少杯", "花了多少", "消费", "记录"))


def _render_day_records_reply(day_label: str, payload: dict[str, Any], message: str) -> str:
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
    enqueue_memory_job(user_id, "thread_summary_refresh", {"thread_key": thread_id}, thread_id)
    enqueue_memory_job(user_id, "memory_extract_from_thread", {"thread_key": thread_id}, thread_id)
    enqueue_memory_job(user_id, "profile_refresh_from_records", {}, thread_id)
    schedule_memory_jobs(limit=10)


def _format_sse(payload: dict[str, Any], *, event: str | None = None, event_id: str | None = None) -> str:
    lines: list[str] = []
    if event:
        lines.append(f"event: {event}")
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False, default=str)}")
    return "\n".join(lines) + "\n\n"


def _extract_text(chunk: Any) -> str:
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
    clean = session_id.strip()
    if clean.startswith("user-") and ":session-" in clean:
        return clean
    if clean.startswith("session-"):
        clean = clean[len("session-") :]
    return f"user-{user_id}:session-{clean}"


@router.post("/chat")
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    request_user_id = getattr(request.state, "user_id", None)
    if not request_user_id:
        raise HTTPException(status_code=401, detail="missing authenticated user")
    user_id = request_user_id
    request_id = (
        getattr(request.state, "request_id", None)
        or request.headers.get("X-Request-ID")
        or request.headers.get("X-Request-Id")
        or uuid4().hex
    )
    thread_id = _session_thread_id(user_id, payload.thread_id)
    request_start = time.perf_counter()
    chat_model = _chat_model()
    budget_snapshot = _daily_budget_snapshot(user_id=user_id, model=chat_model)
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
    repository.create_thread(user_id, thread_id)
    repository.append_message(
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
                    repository.append_message(
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
                    repository.append_message(
                        user_id=user_id,
                        thread_key=thread_id,
                        role="assistant",
                        content=reply_text,
                        request_id=request_id,
                        source="agent_fast_path",
                    )
                    if llm_usage_tokens:
                        record_usage(
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
                    return

            if _is_simple_stats_request(payload.message):
                active_mode = "stats_fast_path"
                period = _detect_stats_period(payload.message) or "all"
                _log_phase(
                    request_id,
                    "stats_fast_path_selected",
                    elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                    period=period,
                )
                stats = get_stats_impl(
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
                repository.append_message(
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
                return

            if _is_recent_records_request(payload.message):
                active_mode = "recent_records_fast_path"
                recent_limit = _recent_records_limit_for_message(payload.message)
                _log_phase(
                    request_id,
                    "recent_records_fast_path_selected",
                    elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                    limit=recent_limit,
                )
                payload_recent = get_recent_records_impl(
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
                repository.append_message(
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
                return

            if _is_day_records_request(payload.message):
                day_anchor = _detect_day_anchor(payload.message)
                if day_anchor:
                    active_mode = "day_records_fast_path"
                    day_label, target_date = day_anchor
                    _log_phase(
                        request_id,
                        "day_records_fast_path_selected",
                        elapsed_ms=round((time.perf_counter() - request_start) * 1000, 2),
                        date=target_date,
                    )
                    payload_day = get_day_impl(
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
                    repository.append_message(
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
                    return

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
                    repository.append_message(
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
                repository.append_message(
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
                record_usage(
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
                repository.append_message(
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
                repository.append_message(
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
