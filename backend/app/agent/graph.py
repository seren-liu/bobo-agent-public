"""Agent 图编排与事件流入口。

本模块负责构建 LangGraph 状态图、初始化可选的 Postgres Checkpointer，
并对外提供统一的事件流式执行入口。

核心职责:
- 构建包含 llm_node / tool_node 的状态图
- 按需初始化 LangGraph 的持久化 Checkpointer
- 为一次对话请求注入 thread_id / request_id / user_id 等运行上下文
- 以事件流形式向上层暴露 Agent 执行过程
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from app.agent.nodes import create_runtime, llm_node, route_after_llm, route_after_tool, tool_node
from app.agent.state import AgentState, reset_agent_context, set_agent_context
from app.core.config import get_settings, to_psycopg_conninfo

_graph = None
_graph_lock = asyncio.Lock()
_checkpointer_cm = None
_checkpointer = None


async def _build_checkpointer() -> Any | None:
    """构建 LangGraph Checkpointer。

    优先使用数据库连接初始化 Postgres 持久化能力。
    如果当前环境未配置数据库，或缺少 langgraph postgres 依赖，
    则返回 None，表示图仍可运行但没有持久化 checkpoint。

    返回:
        Checkpointer 实例；如果无法初始化则返回 None。
    """
    # 从全局配置中获取数据库连接串
    database_url = get_settings().database_url
    if not database_url:
        return None
    database_url = to_psycopg_conninfo(database_url)

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except Exception:
        # 未安装 postgres saver 依赖时降级为无 checkpoint 模式
        return None

    try:
        if hasattr(AsyncPostgresSaver, "from_conn_string"):
            # 兼容新版 API：通过异步上下文管理器创建 saver
            cm = AsyncPostgresSaver.from_conn_string(database_url)
            saver = await cm.__aenter__()
            setup = getattr(saver, "setup", None)
            if callable(setup):
                result = setup()
                if asyncio.iscoroutine(result):
                    await result
            global _checkpointer_cm
            _checkpointer_cm = cm
            return saver

        # 兼容旧版 API：直接构造 saver
        saver = AsyncPostgresSaver(database_url)
        setup = getattr(saver, "setup", None)
        if callable(setup):
            result = setup()
            if asyncio.iscoroutine(result):
                await result
        return saver
    except Exception:
        # checkpoint 初始化失败不阻塞主流程，直接降级
        return None


async def get_agent_graph():
    """获取全局单例 Agent 图。

    通过双重检查 + 异步锁避免并发场景下重复构建状态图。
    图结构为:
    1. START -> llm_node
    2. llm_node 根据是否产生 tool_calls 决定进入 tool_node 或结束
    3. tool_node 执行完工具后回到 llm_node，直到步数耗尽或结束

    返回:
        已编译的 LangGraph 状态图实例。
    """
    global _graph, _checkpointer
    if _graph is not None:
        return _graph

    async with _graph_lock:
        if _graph is not None:
            return _graph

        try:
            from langgraph.graph import END, START, StateGraph
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("langgraph is required for agent graph") from exc

        async def _llm(state: AgentState) -> dict[str, Any]:
            return await llm_node(state, await create_runtime())

        async def _tool(state: AgentState) -> dict[str, Any]:
            return await tool_node(state, await create_runtime())

        # 定义 Agent 状态图中的两个核心节点：LLM 决策节点和工具执行节点
        workflow = StateGraph(AgentState)
        workflow.add_node("llm_node", _llm)
        workflow.add_node("tool_node", _tool)

        # 首次进入图时先由 LLM 判断意图，并决定是否需要调用工具
        workflow.add_edge(START, "llm_node")
        workflow.add_conditional_edges(
            "llm_node",
            route_after_llm,
            {
                "tool": "tool_node",
                "end": END,
            },
        )
        workflow.add_conditional_edges(
            "tool_node",
            route_after_tool,
            {
                "llm": "llm_node",
                "end": END,
            },
        )

        # 编译时挂载可选 checkpointer，用于 thread 级状态持久化
        _checkpointer = await _build_checkpointer()
        _graph = workflow.compile(checkpointer=_checkpointer)

    return _graph


async def close_agent_runtime() -> None:
    """关闭 checkpointer 相关资源。

    主要用于应用关闭时释放 AsyncPostgresSaver 持有的连接与上下文。
    """
    global _checkpointer_cm
    if _checkpointer_cm is None:
        return

    try:
        await _checkpointer_cm.__aexit__(None, None, None)
    finally:
        _checkpointer_cm = None


async def stream_agent_events(
    *,
    message: str,
    user_id: str,
    thread_id: str,
    max_steps: int = 10,
    request_id: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """以事件流形式执行一次 Agent 对话。

    执行流程:
    1. 构造初始 AgentState
    2. 将 user_id / thread_id / request_id 写入上下文
    3. 调用 LangGraph 的 astream_events 流式执行
    4. 在 finally 中重置上下文，避免污染后续请求

    参数:
        message: 用户本轮输入消息。
        user_id: 当前用户标识。
        thread_id: 对话线程标识，用于 checkpoint 和 memory retrieval。
        max_steps: 本轮图执行的最大步数，防止无限循环。
        request_id: 可选请求标识；未传时自动生成。

    返回:
        LangGraph 产生的事件异步生成器。
    """
    graph = await get_agent_graph()
    resolved_request_id = request_id or uuid4().hex

    # 初始化本轮对话状态，messages 以 LangGraph 可识别的二元组格式输入
    inputs: AgentState = {
        "messages": [("user", message)],
        "user_id": user_id,
        "request_id": resolved_request_id,
        "intent": None,
        "tool_result": None,
        "max_steps": max_steps,
    }
    config = {"configurable": {"thread_id": thread_id}}

    # 将请求级上下文注入 ContextVar，供工具调用与审计链路复用
    token = set_agent_context(
        {
            "user_id": user_id,
            "thread_id": thread_id,
            "request_id": resolved_request_id,
            "source": "agent",
        }
    )
    try:
        async for event in graph.astream_events(inputs, config=config, version="v2"):
            yield event
    finally:
        # 无论执行成功还是失败，都要清理上下文避免串请求
        reset_agent_context(token)
