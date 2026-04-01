from __future__ import annotations

import json
import os
from collections.abc import Awaitable
from typing import Any

from app.agent.state import get_agent_context, resolve_agent_user_id
from app.core.config import get_settings
from app.memory.retrieval import build_agent_prompt_context
from app.tooling import (
    get_calendar_impl as _get_calendar_impl,
    get_local_tools,
    get_stats_impl as _get_stats_impl,
    record_drink_impl as _record_drink_impl,
    search_menu_impl as _search_menu_impl,
    update_menu_impl as _update_menu_impl,
)
from app.tooling.context import audit_tool_event

try:
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.tools import BaseTool
except Exception:  # pragma: no cover
    AIMessage = Any
    ToolMessage = Any
    BaseTool = Any


SYSTEM_PROMPT = (
    "你是 Bobo 奶茶智能助手。"
    "优先使用工具获取事实（菜单检索、统计、日历、记录写入、菜单更新），"
    "输出简洁、明确、可执行。"
)


def _current_identity(explicit_user_id: str | None = None) -> str:
    return resolve_agent_user_id(explicit_user_id, required=True)


def _fallback_tools() -> list[BaseTool]:
    return get_local_tools()


def _agent_tool_mode() -> str:
    settings = get_settings()
    explicit = settings.agent_tool_mode.strip().lower()
    if explicit:
        return explicit
    return "mcp_remote" if settings.env.lower() in {"prod", "production"} else "hybrid_debug"


def _mcp_service_token() -> str:
    settings = get_settings()
    return settings.mcp_service_token or f"{settings.jwt_secret}:mcp"


def _mcp_headers() -> dict[str, str]:
    context = get_agent_context() or {}
    headers = {
        "Authorization": f"Bearer {_mcp_service_token()}",
        "X-Bobo-Source": str(context.get("source") or "agent"),
    }
    request_id = str(context.get("request_id") or "").strip()
    if request_id:
        headers["X-Request-Id"] = request_id
    thread_id = str(context.get("thread_id") or "").strip()
    if thread_id:
        headers["X-Bobo-Thread-Id"] = thread_id
    return headers


def _mcp_url() -> str:
    url = get_settings().mcp_server_url.strip()
    if not url:
        return "http://localhost:8000/mcp/"
    return url if url.endswith("/") else f"{url}/"


async def get_mcp_tools() -> list[BaseTool]:
    """Load MCP tools for production and only fallback locally in explicit dev modes."""
    settings = get_settings()
    mode = _agent_tool_mode()

    if mode == "local_fallback":
        return _fallback_tools()

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except Exception:
        if mode == "hybrid_debug":
            return _fallback_tools()
        raise RuntimeError("langchain-mcp-adapters is required for MCP-first agent mode")

    try:
        client = MultiServerMCPClient(
            {
                "bobo": {
                    "transport": settings.mcp_transport,
                    "url": _mcp_url(),
                    "headers": _mcp_headers(),
                }
            }
        )
        tools = await client.get_tools()
        if tools:
            return list(tools)
        if mode == "hybrid_debug":
            return _fallback_tools()
        raise RuntimeError("MCP server returned no tools")
    except Exception:
        if mode == "hybrid_debug":
            return _fallback_tools()
        raise


def _build_tool_lookup(tools: list[BaseTool]) -> dict[str, BaseTool]:
    return {getattr(t, "name", ""): t for t in tools if getattr(t, "name", None)}


async def create_runtime() -> dict[str, Any]:
    try:
        from langchain_openai import ChatOpenAI
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("langchain-openai is required for agent llm node") from exc

    api_key = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    model = os.getenv("QWEN_CHAT_MODEL", "qwen3-32b")

    llm = ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=0.2,
        streaming=True,
    )
    tools = await get_mcp_tools()
    return {
        "llm": llm.bind_tools(tools),
        "tools": tools,
        "tool_lookup": _build_tool_lookup(tools),
    }


async def llm_node(state: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    llm = runtime["llm"]
    messages = list(state.get("messages", []))

    if not messages:
        return {
            "messages": [AIMessage(content="请先告诉我你想记录或查询什么奶茶信息。")],
        }

    first = messages[0]
    has_system = isinstance(first, tuple) and len(first) >= 1 and first[0] == "system"
    base_messages = messages if has_system else [("system", SYSTEM_PROMPT), *messages]
    context = get_agent_context() or {}
    thread_id = str(context.get("thread_id") or "")
    try:
        user_id = _current_identity(state.get("user_id"))
    except PermissionError:
        user_id = ""
    memory_messages = build_agent_prompt_context(user_id, thread_id, messages) if thread_id and user_id else []
    prompt_messages = [base_messages[0], *memory_messages, *base_messages[1:]] if has_system or base_messages else base_messages

    response = await llm.ainvoke(prompt_messages)
    return {"messages": [response]}


async def _invoke_tool(tool_obj: BaseTool, args: dict[str, Any]) -> Any:
    maybe_ainvoke = getattr(tool_obj, "ainvoke", None)
    if callable(maybe_ainvoke):
        return await maybe_ainvoke(args)

    maybe_invoke = getattr(tool_obj, "invoke", None)
    if callable(maybe_invoke):
        result = maybe_invoke(args)
        if isinstance(result, Awaitable):
            return await result
        return result

    fn = getattr(tool_obj, "func", None)
    if callable(fn):
        result = fn(**args)
        if isinstance(result, Awaitable):
            return await result
        return result

    raise RuntimeError(f"tool {getattr(tool_obj, 'name', '<unknown>')} is not invokable")


def _with_tool_context(args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    context = get_agent_context() or {}
    payload = dict(args)
    payload["user_id"] = _current_identity(state.get("user_id"))
    payload["request_id"] = context.get("request_id") or state.get("request_id")
    payload["thread_id"] = context.get("thread_id")
    payload["source"] = context.get("source", "agent")
    return payload


async def tool_node(state: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    remaining = int(state.get("max_steps", 10))
    if remaining <= 0:
        return {"max_steps": 0}

    messages = list(state.get("messages", []))
    if not messages:
        return {"max_steps": max(remaining - 1, 0)}

    last = messages[-1]
    tool_calls = list(getattr(last, "tool_calls", None) or [])
    if not tool_calls:
        return {"max_steps": max(remaining - 1, 0)}

    tool_lookup = runtime["tool_lookup"]
    tool_messages: list[ToolMessage] = []
    latest_result: dict[str, Any] | None = None

    for call in tool_calls:
        name = call.get("name")
        args = call.get("args") or {}
        tool_id = call.get("id") or ""
        contextualized_args = _with_tool_context(args, state)

        tool_obj = tool_lookup.get(name)
        if tool_obj is None:
            payload = {"ok": False, "error": f"unknown_tool:{name}"}
        else:
            try:
                audit_tool_event(
                    name or "unknown",
                    "invoke_start",
                    user_id=contextualized_args["user_id"],
                    request_id=contextualized_args.get("request_id"),
                    thread_id=contextualized_args.get("thread_id"),
                    source=contextualized_args.get("source"),
                    args=list(contextualized_args.keys()),
                )
                data = await _invoke_tool(tool_obj, contextualized_args)
                payload = data if isinstance(data, dict) else {"result": data}
                audit_tool_event(
                    name or "unknown",
                    "invoke_success",
                    user_id=contextualized_args["user_id"],
                    request_id=contextualized_args.get("request_id"),
                    thread_id=contextualized_args.get("thread_id"),
                    source=contextualized_args.get("source"),
                    tool_id=tool_id,
                )
            except Exception as exc:
                audit_tool_event(
                    name or "unknown",
                    "invoke_error",
                    user_id=contextualized_args["user_id"],
                    request_id=contextualized_args.get("request_id"),
                    thread_id=contextualized_args.get("thread_id"),
                    source=contextualized_args.get("source"),
                    error=str(exc),
                )
                payload = {"ok": False, "error": str(exc)}

        latest_result = payload
        tool_messages.append(
            ToolMessage(
                content=json.dumps(payload, ensure_ascii=False, default=str),
                tool_call_id=tool_id,
                name=name,
            )
        )

    return {
        "messages": tool_messages,
        "tool_result": latest_result,
        "max_steps": max(remaining - 1, 0),
    }


def route_after_llm(state: dict[str, Any]) -> str:
    if int(state.get("max_steps", 10)) <= 0:
        return "end"

    messages = state.get("messages", [])
    if not messages:
        return "end"

    last = messages[-1]
    tool_calls = getattr(last, "tool_calls", None) or []
    return "tool" if tool_calls else "end"


def route_after_tool(state: dict[str, Any]) -> str:
    return "end" if int(state.get("max_steps", 10)) <= 0 else "llm"
