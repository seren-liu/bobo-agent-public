from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from typing import Annotated

from typing_extensions import TypedDict

try:
    from langgraph.graph.message import add_messages
except Exception:  # pragma: no cover
    def add_messages(left, right):
        if left is None:
            return right
        if right is None:
            return left
        return list(left) + list(right)


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    user_id: str
    request_id: str | None
    intent: str | None
    tool_result: dict | None
    max_steps: int


class AgentExecutionContext(TypedDict, total=False):
    user_id: str
    request_id: str
    thread_id: str
    source: str


_agent_context: ContextVar[AgentExecutionContext | None] = ContextVar("bobo_agent_context", default=None)
_audit_logger = logging.getLogger("bobo.audit")


def set_agent_context(context: AgentExecutionContext) -> Token[AgentExecutionContext | None]:
    return _agent_context.set(context)


def reset_agent_context(token: Token[AgentExecutionContext | None]) -> None:
    _agent_context.reset(token)


def get_agent_context() -> AgentExecutionContext | None:
    return _agent_context.get()


def resolve_agent_user_id(explicit_user_id: str | None = None, *, required: bool = True) -> str:
    context = get_agent_context() or {}
    user_id = (explicit_user_id or context.get("user_id") or "").strip()
    if user_id:
        return user_id
    if required:
        raise PermissionError("authenticated user identity is required")
    return "anonymous"


def audit_agent_event(kind: str, **fields) -> None:
    payload = {"kind": kind, **fields}
    _audit_logger.info(json.dumps(payload, ensure_ascii=False, default=str))
