from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.memory import repository
from app.services.memory_vectors import MemoryVectorService

_CATEGORY_LABELS = {
    "fruit_tea": "果茶",
    "milk_tea": "奶茶",
    "light_milk_tea": "轻乳茶",
    "pure_tea": "纯茶",
    "lemon_tea": "柠檬茶",
    "coffee": "咖啡",
}


def _humanize_category(value: str) -> str:
    return _CATEGORY_LABELS.get(value, value)


def _profile_context_blocks(profile: dict[str, Any]) -> list[str]:
    drink = profile.get("drink_preferences") or {}
    budget = profile.get("budget_preferences") or {}
    interaction = profile.get("interaction_preferences") or {}

    blocks: list[str] = []

    reply_style = interaction.get("reply_style")
    if reply_style:
        blocks.append(f"回答风格：{reply_style}")

    budget_lines: list[str] = []
    if budget.get("soft_price_ceiling") is not None:
        budget_lines.append(f"{budget['soft_price_ceiling']} 元以内优先")
    if budget.get("price_sensitive") is True:
        budget_lines.append("价格敏感")
    if budget_lines:
        blocks.append("预算偏好：" + " / ".join(budget_lines))

    drink_lines: list[str] = []
    default_sugar = drink.get("default_sugar")
    default_ice = drink.get("default_ice")
    if default_sugar or default_ice:
        drink_lines.append(f"默认糖冰：{default_sugar or '未设定'} / {default_ice or '未设定'}")
    preferred_brands = drink.get("preferred_brands") or []
    if preferred_brands:
        drink_lines.append(f"偏好品牌：{', '.join(preferred_brands[:3])}")
    preferred_categories = drink.get("preferred_categories") or []
    if preferred_categories:
        labels = [_humanize_category(str(item)) for item in preferred_categories[:3]]
        drink_lines.append(f"偏好品类：{', '.join(labels)}")
    if drink_lines:
        blocks.append("\n".join(drink_lines))

    return blocks


def load_profile_summary(user_id: str) -> str:
    profile = repository.get_profile(user_id)
    return "\n".join(_profile_context_blocks(profile))


def build_memory_context_blocks(user_id: str, thread_key: str, query: str) -> dict[str, Any]:
    profile = repository.get_profile(user_id)
    profile_blocks = _profile_context_blocks(profile)
    thread_summary = load_latest_thread_summary(user_id, thread_key)
    memories = search_relevant_memories(user_id, query=query, scope="recommendation")
    return {
        "profile_blocks": profile_blocks,
        "profile_summary": "\n".join(profile_blocks),
        "thread_summary": thread_summary,
        "memories": memories,
    }


def load_latest_thread_summary(user_id: str, thread_key: str) -> str:
    summary = repository.latest_summary(user_id, thread_key)
    if not summary:
        return ""
    parts = [str(summary.get("summary_text") or "").strip()]
    open_slots = list(summary.get("open_slots") or [])
    if open_slots:
        parts.append("待跟进：" + "；".join(str(item) for item in open_slots[:3]))
    return "\n".join(part for part in parts if part)


def _matches_query(memory: dict[str, Any], query: str) -> int:
    if not query:
        return 1
    score = 0
    content = str(memory.get("content") or "")
    for token in query.lower().split():
        if token and token in content.lower():
            score += 1
    return score


def search_relevant_memories(user_id: str, query: str, scope: str | None = None, top_k: int | None = None) -> list[dict[str, Any]]:
    items = repository.list_memories(user_id)
    now = datetime.now(UTC)
    filtered: list[dict[str, Any]] = []
    for item in items:
        expires_at = item.get("expires_at")
        if expires_at and expires_at < now:
            continue
        if scope and item.get("scope") not in {scope, "global"}:
            continue
        filtered.append(item)

    limit = top_k if top_k is not None else max(int(get_settings().memory_semantic_top_k or 4), 1)
    filtered.sort(
        key=lambda item: (
            _matches_query(item, query),
            float(item.get("salience") or 0),
            float(item.get("confidence") or 0),
            item.get("updated_at") or item.get("created_at") or now,
        ),
        reverse=True,
    )
    chosen = filtered[:limit]

    if query.strip():
        vector_hits = MemoryVectorService().search_memory_items(
            user_id=user_id,
            query=query,
            scope=scope,
            top_k=limit,
        )
        if vector_hits:
            by_id = {str(item["id"]): item for item in filtered}
            vector_ranked: list[dict[str, Any]] = []
            for hit in vector_hits:
                item = by_id.get(str(hit["id"]))
                if not item:
                    continue
                merged = dict(item)
                merged["vector_score"] = float(hit.get("score") or 0)
                vector_ranked.append(merged)
            if vector_ranked:
                seen: set[str] = set()
                chosen = []
                for item in vector_ranked + filtered:
                    item_id = str(item["id"])
                    if item_id in seen:
                        continue
                    seen.add(item_id)
                    chosen.append(item)
                    if len(chosen) >= limit:
                        break

    for item in chosen:
        repository.touch_memory_item(user_id, str(item["id"]))
    return chosen


def load_memory_context(user_id: str, thread_key: str, query: str) -> dict[str, Any]:
    return build_memory_context_blocks(user_id, thread_key, query)


def build_agent_prompt_context(user_id: str, thread_key: str, recent_messages: list[Any]) -> list[tuple[str, str]]:
    latest_user_text = ""
    for message in reversed(recent_messages):
        if isinstance(message, tuple) and len(message) >= 2 and message[0] == "user":
            latest_user_text = str(message[1])
            break
        role = getattr(message, "type", None) or getattr(message, "role", None)
        if role == "human":
            latest_user_text = str(getattr(message, "content", ""))
            break

    context = load_memory_context(user_id, thread_key, latest_user_text)
    prompts: list[tuple[str, str]] = []
    for block in context["profile_blocks"]:
        prompts.append(("system", f"用户长期画像（优先）：\n{block}"))
    if context["thread_summary"]:
        prompts.append(("system", f"当前会话摘要：\n{context['thread_summary']}"))
    if context["memories"]:
        memory_lines = [f"- {item['content']}" for item in context["memories"]]
        prompts.append(("system", "相关长期记忆：\n" + "\n".join(memory_lines)))
    return prompts
