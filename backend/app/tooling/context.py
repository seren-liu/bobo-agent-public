from __future__ import annotations

from typing import Any

from app.agent.state import audit_agent_event, get_agent_context, resolve_agent_user_id


def resolve_tool_context(
    *,
    user_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    source: str | None = None,
    required_user: bool = True,
) -> dict[str, str]:
    context = get_agent_context() or {}
    resolved_user_id = resolve_agent_user_id(user_id, required=required_user)
    return {
        "user_id": resolved_user_id,
        "request_id": request_id or context.get("request_id") or "",
        "thread_id": thread_id or context.get("thread_id") or "",
        "source": source or context.get("source") or "agent",
    }


def audit_tool_event(tool_name: str, stage: str, *, user_id: str, **fields: Any) -> None:
    context = get_agent_context() or {}
    audit_agent_event(
        "tool",
        stage=stage,
        tool=tool_name,
        user_id=user_id,
        request_id=fields.pop("request_id", None) or context.get("request_id"),
        thread_id=fields.pop("thread_id", None) or context.get("thread_id"),
        source=fields.pop("source", None) or context.get("source", "agent"),
        **fields,
    )
