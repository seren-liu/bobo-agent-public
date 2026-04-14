from __future__ import annotations

from datetime import UTC, datetime
import os
import re
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

_DEFAULT_MEMORY_PROMPT_MAX_CHARS = 760
_DEFAULT_MEMORY_PROMPT_MAX_ITEMS = 4
_DEFAULT_MEMORY_PROMPT_PER_ITEM_CHARS = 180
_DEFAULT_MEMORY_PROMPT_PROFILE_CHARS = 240
_DEFAULT_MEMORY_PROMPT_THREAD_CHARS = 220
_DEFAULT_MEMORY_PROMPT_MEMORIES_CHARS = 320


def _humanize_category(value: str) -> str:
    return _CATEGORY_LABELS.get(value, value)


def _budget_int(env_name: str, default: int) -> int:
    raw = os.getenv(env_name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(int(str(raw).strip()), 1)
    except ValueError:
        return default


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _truncate_text(text: str, limit: int) -> str:
    value = str(text or "")
    if limit <= 0 or len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return value[: max(limit - 1, 0)] + "…"


def _approx_token_count(text: str) -> int:
    value = str(text or "")
    return max((len(value) + 1) // 2, 0)


def _dedupe_texts(texts: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for text in texts:
        normalized = _normalize_text(text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(text)
    return deduped


def _budget_prompt_sections(
    prompts: list[tuple[str, str]],
    *,
    total_char_budget: int,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    sections: list[tuple[str, str, str]] = []
    for role, content in prompts:
        label = "other"
        if content.startswith("用户长期画像（优先）：\n"):
            label = "profile"
        elif content.startswith("当前会话摘要：\n"):
            label = "thread_summary"
        elif content.startswith("相关长期记忆：\n"):
            label = "memories"
        sections.append((role, content, label))

    section_caps = {
        "profile": _budget_int("BOBO_MEMORY_PROMPT_PROFILE_CHARS", _DEFAULT_MEMORY_PROMPT_PROFILE_CHARS),
        "thread_summary": _budget_int("BOBO_MEMORY_PROMPT_THREAD_CHARS", _DEFAULT_MEMORY_PROMPT_THREAD_CHARS),
        "memories": _budget_int("BOBO_MEMORY_PROMPT_MEMORIES_CHARS", _DEFAULT_MEMORY_PROMPT_MEMORIES_CHARS),
        "other": total_char_budget,
    }
    seen: set[str] = set()
    output: list[tuple[str, str]] = []
    used_chars = 0
    truncated = False
    dropped_sections: list[str] = []
    section_usage: dict[str, int] = {"profile": 0, "thread_summary": 0, "memories": 0, "other": 0}

    for role, content, label in sections:
        normalized = _normalize_text(content)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)

        remaining_total = total_char_budget - used_chars
        if remaining_total <= 0:
            truncated = True
            dropped_sections.append(label)
            continue

        section_cap = section_caps.get(label, total_char_budget)
        remaining_section = section_cap - section_usage.get(label, 0)
        if remaining_section <= 0:
            truncated = True
            dropped_sections.append(label)
            continue

        limit = min(len(content), remaining_total, remaining_section)
        clipped = _truncate_text(content, limit)
        if clipped != content:
            truncated = True
        output.append((role, clipped))
        used_chars += len(clipped)
        section_usage[label] = section_usage.get(label, 0) + len(clipped)

    rendered_text = "\n".join(content for _, content in output)
    diagnostics = {
        "prompt_count": len(output),
        "char_count": len(rendered_text),
        "estimated_tokens": _approx_token_count(rendered_text),
        "total_char_budget": total_char_budget,
        "section_usage": section_usage,
        "truncated": truncated,
        "dropped_sections": dropped_sections,
        "unique_prompt_count": len(seen),
    }
    return output, diagnostics


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
    memories = search_relevant_memories(
        user_id,
        query=query,
        scope="recommendation",
        top_k=_budget_int("BOBO_MEMORY_PROMPT_MAX_ITEMS", _DEFAULT_MEMORY_PROMPT_MAX_ITEMS),
    )
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


def _build_memory_vector_service(user_id: str) -> MemoryVectorService:
    try:
        return MemoryVectorService(user_id=user_id)
    except TypeError:
        # Test doubles may not accept constructor arguments.
        return MemoryVectorService()


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
        vector_hits = _build_memory_vector_service(user_id).search_memory_items(
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


def _build_agent_prompt_context_v1(
    user_id: str,
    thread_key: str,
    recent_messages: list[Any],
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
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
        per_item_budget = _budget_int("BOBO_MEMORY_PROMPT_PER_ITEM_CHARS", _DEFAULT_MEMORY_PROMPT_PER_ITEM_CHARS)
        memory_lines = [
            f"- {_truncate_text(str(item.get('content') or ''), per_item_budget)}"
            for item in context["memories"]
        ]
        memory_lines = _dedupe_texts(memory_lines)
        prompts.append(("system", "相关长期记忆：\n" + "\n".join(memory_lines)))

    return _budget_prompt_sections(
        prompts,
        total_char_budget=_budget_int("BOBO_MEMORY_PROMPT_MAX_CHARS", _DEFAULT_MEMORY_PROMPT_MAX_CHARS),
    )


_CONTEXT_BUILDERS = {
    "bobo-agent-memory-context.v1": _build_agent_prompt_context_v1,
}


def build_agent_prompt_context(
    user_id: str,
    thread_key: str,
    recent_messages: list[Any],
    *,
    version: str | None = None,
    include_metadata: bool = False,
) -> list[tuple[str, str]] | dict[str, Any]:
    selected_version = (version or get_settings().agent_memory_context_version or "bobo-agent-memory-context.v1").strip()
    builder = _CONTEXT_BUILDERS.get(selected_version)
    if builder is None:
        selected_version = "bobo-agent-memory-context.v1"
        builder = _CONTEXT_BUILDERS[selected_version]

    budgeted_prompts, diagnostics = builder(user_id, thread_key, recent_messages)
    if include_metadata:
        return {
            "prompts": budgeted_prompts,
            "diagnostics": diagnostics,
            "rendered_text": "\n".join(content for _, content in budgeted_prompts),
            "context_version": selected_version,
        }
    return budgeted_prompts
