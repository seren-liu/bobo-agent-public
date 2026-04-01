from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from app.agent.nodes import create_runtime, llm_node, route_after_llm, route_after_tool, tool_node
from app.agent.state import AgentState, reset_agent_context, set_agent_context
from app.core.config import get_settings

_graph = None
_graph_lock = asyncio.Lock()
_checkpointer_cm = None
_checkpointer = None


async def _build_checkpointer() -> Any | None:
    database_url = get_settings().database_url
    if not database_url:
        return None

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except Exception:
        return None

    try:
        if hasattr(AsyncPostgresSaver, "from_conn_string"):
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

        saver = AsyncPostgresSaver(database_url)
        setup = getattr(saver, "setup", None)
        if callable(setup):
            result = setup()
            if asyncio.iscoroutine(result):
                await result
        return saver
    except Exception:
        return None


async def get_agent_graph():
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

        workflow = StateGraph(AgentState)
        workflow.add_node("llm_node", _llm)
        workflow.add_node("tool_node", _tool)

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

        _checkpointer = await _build_checkpointer()
        _graph = workflow.compile(checkpointer=_checkpointer)

    return _graph


async def close_agent_runtime() -> None:
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
    graph = await get_agent_graph()
    resolved_request_id = request_id or uuid4().hex
    inputs: AgentState = {
        "messages": [("user", message)],
        "user_id": user_id,
        "request_id": resolved_request_id,
        "intent": None,
        "tool_result": None,
        "max_steps": max_steps,
    }
    config = {"configurable": {"thread_id": thread_id}}

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
        reset_agent_context(token)
