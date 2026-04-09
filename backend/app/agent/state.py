"""Agent 状态与执行上下文模块。

本模块定义 LangGraph Agent 运行时使用的状态结构，
以及跨函数传播的执行上下文（如 user_id、request_id、thread_id）。

核心概念:
- AgentState: 图执行期间在各个节点之间流转的共享状态
- AgentExecutionContext: 不进入图状态、但需要跨调用链透传的请求上下文
- ContextVar: 在异步/并发场景中安全保存当前请求上下文
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from typing import Annotated

from typing_extensions import TypedDict

try:
    from langgraph.graph.message import add_messages
except Exception:  # pragma: no cover
    # 在缺少 langgraph 依赖时，退化为简单列表拼接，
    # 这样本地导入和测试仍然可运行。
    def add_messages(left, right):
        if left is None:
            return right
        if right is None:
            return left
        return list(left) + list(right)


class AgentState(TypedDict):
    """LangGraph Agent 的共享状态结构。

    字段说明:
        messages: 图中累计的消息列表，通过 `add_messages` 进行增量合并。
        user_id: 当前请求所属用户。
        request_id: 请求追踪 ID，用于日志与审计。
        intent: 当前轮识别出的意图，可为空。
        tool_result: 最近一次工具调用结果，可为空。
        max_steps: 图执行的最大步数，防止循环或 runaway execution。
    """

    # LangGraph 会在节点返回增量消息时，通过 add_messages 把历史消息与新消息合并。
    messages: Annotated[list, add_messages]
    user_id: str
    request_id: str | None
    intent: str | None
    tool_result: dict | None
    max_steps: int


class AgentExecutionContext(TypedDict, total=False):
    """Agent 执行上下文。

    这类信息通常与一次请求强绑定，但不一定适合塞进图状态中，
    因此通过 ContextVar 在调用链中透传。

    字段说明:
        user_id: 当前认证用户。
        request_id: 请求追踪 ID。
        thread_id: 会话线程 ID。
        source: 请求来源，如 api / mcp / agent。
    """
    user_id: str
    request_id: str
    thread_id: str
    source: str


# 当前请求的 Agent 执行上下文。
# 使用 ContextVar 而不是全局变量，是为了兼容异步并发请求隔离。
_agent_context: ContextVar[AgentExecutionContext | None] = ContextVar("bobo_agent_context", default=None)
_audit_logger = logging.getLogger("bobo.audit")


def set_agent_context(context: AgentExecutionContext) -> Token[AgentExecutionContext | None]:
    """设置当前请求的 Agent 执行上下文。

    参数:
        context: 要写入当前上下文槽位的执行上下文字典。

    返回:
        ContextVar token，可用于后续 reset 恢复现场。
    """
    return _agent_context.set(context)


def reset_agent_context(token: Token[AgentExecutionContext | None]) -> None:
    """恢复之前保存的 Agent 执行上下文。

    通常与 `set_agent_context` 成对使用，避免请求结束后上下文泄漏到下一次调用。
    """
    _agent_context.reset(token)


def get_agent_context() -> AgentExecutionContext | None:
    """获取当前请求的 Agent 执行上下文。"""
    return _agent_context.get()


def resolve_agent_user_id(explicit_user_id: str | None = None, *, required: bool = True) -> str:
    """解析当前调用应该使用的 user_id。

    解析优先级:
    1. 显式传入的 `explicit_user_id`
    2. 当前 ContextVar 中的 `user_id`
    3. 若 `required=False`，返回匿名用户标识

    参数:
        explicit_user_id: 调用方显式传入的 user_id。
        required: 是否要求必须存在认证用户。

    返回:
        解析出的 user_id。

    异常:
        PermissionError: 当 required=True 且无法解析出用户身份时抛出。
    """
    context = get_agent_context() or {}
    user_id = (explicit_user_id or context.get("user_id") or "").strip()
    if user_id:
        return user_id
    if required:
        raise PermissionError("authenticated user identity is required")
    return "anonymous"


def audit_agent_event(kind: str, **fields) -> None:
    """写入一条 Agent 审计日志。

    参数:
        kind: 事件类型，如 tool_call / graph_start / graph_end。
        **fields: 附加结构化字段。
    """
    # 审计日志统一序列化为 JSON，方便后续被日志系统检索和聚合。
    payload = {"kind": kind, **fields}
    _audit_logger.info(json.dumps(payload, ensure_ascii=False, default=str))
