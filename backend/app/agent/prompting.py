from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import get_settings
from app.memory.retrieval import build_agent_prompt_context

logger = logging.getLogger("bobo.agent.prompting")

SYSTEM_PROMPT_V1 = (
    "你是 Bobo 奶茶智能助手。"
    "优先使用工具获取事实（菜单检索、统计、日历、记录写入、菜单更新），"
    "输出简洁、明确、可执行。"
)

_SYSTEM_PROMPTS: dict[str, str] = {
    "bobo-agent-system.v1": SYSTEM_PROMPT_V1,
}


def resolve_system_prompt(version: str | None = None) -> tuple[str, str]:
    selected = (version or get_settings().agent_system_prompt_version or "bobo-agent-system.v1").strip()
    prompt = _SYSTEM_PROMPTS.get(selected)
    if prompt is None:
        selected = "bobo-agent-system.v1"
        prompt = _SYSTEM_PROMPTS[selected]
    return selected, prompt


def build_prompt_bundle(
    *,
    user_id: str,
    thread_id: str,
    messages: list[Any],
) -> dict[str, Any]:
    settings = get_settings()
    system_prompt_version, system_prompt = resolve_system_prompt(settings.agent_system_prompt_version)
    memory_bundle = build_agent_prompt_context(
        user_id,
        thread_id,
        messages,
        version=settings.agent_memory_context_version,
        include_metadata=True,
    )
    bundle = {
        "system_prompt": system_prompt,
        "system_prompt_version": system_prompt_version,
        "context_version": memory_bundle.get("context_version") or settings.agent_memory_context_version,
        "memory_bundle": memory_bundle,
    }
    logger.info(
        json.dumps(
            {
                "event": "agent_prompt_bundle",
                "system_prompt_version": bundle["system_prompt_version"],
                "context_version": bundle["context_version"],
                "diagnostics": memory_bundle.get("diagnostics") or {},
            },
            ensure_ascii=False,
            default=str,
        )
    )
    return bundle
