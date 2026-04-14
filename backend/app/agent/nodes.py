"""Agent 节点实现与运行时装配模块。

本模块负责:
- 创建 Agent 运行时（LLM、工具列表、工具查找表）
- 实现 llm_node 与 tool_node 两个核心节点
- 决定优先走 MCP 远程工具还是本地 fallback 工具
- 为工具调用补齐审计与上下文信息
"""

from __future__ import annotations

import json
import os
import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

from app.agent.prompting import build_prompt_bundle
from app.agent.state import get_agent_context, resolve_agent_user_id
from app.core.config import get_settings, is_production_env
from app.core.resilience import DependencyError, call_with_resilience, get_circuit_breaker
from app.core.tool_errors import build_tool_error_payload
from app.tooling import (
    get_calendar_impl as _get_calendar_impl,
    get_local_tools,
    get_stats_impl as _get_stats_impl,
    record_drink_impl as _record_drink_impl,
    search_menu_impl as _search_menu_impl,
    update_menu_impl as _update_menu_impl,
)
from app.tooling.context import audit_tool_event
from app.tooling.validation import validate_tool_args, validate_tool_result

try:
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.tools import BaseTool
except Exception:  # pragma: no cover
    AIMessage = Any
    ToolMessage = Any
    BaseTool = Any


_runtime_cache: dict[str, Any] | None = None
_runtime_lock = asyncio.Lock()
logger = logging.getLogger("bobo.agent.nodes")


def _current_identity(explicit_user_id: str | None = None) -> str:
    """解析当前 Agent 运行时对应的用户身份。"""
    return resolve_agent_user_id(explicit_user_id, required=True)


def _fallback_tools() -> list[BaseTool]:
    """获取本地 fallback 工具集合。"""
    return get_local_tools()


def _agent_tool_mode() -> str:
    """确定当前 Agent 的工具加载模式。

    优先使用显式配置的 AGENT_TOOL_MODE。
    若未配置，则生产环境默认 mcp_remote，开发环境默认 hybrid_debug。

    返回:
        工具模式字符串，如 mcp_remote / hybrid_debug / local_fallback。
    """
    settings = get_settings()
    explicit = settings.agent_tool_mode.strip().lower()
    if explicit:
        return explicit
    return "mcp_remote" if settings.env.lower() in {"prod", "production"} else "hybrid_debug"


def _mcp_service_token() -> str:
    """获取 MCP 服务级调用 token。"""
    settings = get_settings()
    if settings.mcp_service_token:
        return settings.mcp_service_token
    if not is_production_env(settings.env):
        return f"{settings.jwt_secret}:mcp"
    raise RuntimeError("MCP_SERVICE_TOKEN must be configured in production")


def _mcp_headers() -> dict[str, str]:
    """构造访问 MCP 服务时附带的请求头。

    这些头部会把当前 Agent 请求的来源、request_id、thread_id
    继续透传给下游 MCP 服务，便于鉴权、审计与链路追踪。

    返回:
        MCP 请求头字典。
    """
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
    """解析 MCP Server URL，并补齐末尾斜杠。"""
    url = get_settings().mcp_server_url.strip()
    if not url:
        return "http://localhost:8000/mcp/"
    return url if url.endswith("/") else f"{url}/"


def _tool_timeout_seconds(tool_name: str) -> float:
    settings = get_settings()
    if tool_name in {"record_drink", "update_menu"}:
        return max(float(settings.agent_tool_write_timeout_seconds or 0), 0.1)
    return max(float(settings.agent_tool_timeout_seconds or 0), 0.1)


def _dependency_breaker(name: str):
    settings = get_settings()
    return get_circuit_breaker(
        name,
        failure_threshold=settings.dependency_circuit_failure_threshold,
        recovery_timeout_seconds=settings.dependency_circuit_recovery_seconds,
    )


def _llm_degraded_reply(state: dict[str, Any], error: DependencyError) -> str:
    latest_tool = state.get("tool_result")
    if isinstance(latest_tool, dict) and latest_tool.get("ok") is not False:
        return "我已经拿到工具结果了，但当前总结服务有点忙。你可以直接换个更短的问题，我会继续基于刚才的数据帮你。"
    if error.category == "timeout":
        return "我这边的推理服务刚刚超时了。可以先试更直接的问法，比如“今天喝了什么”“这周喝了几杯”或“推荐喜茶果茶”。"
    return "我这边的推理服务暂时不太稳定。可以先试更直接的查询，我会优先走更快的路径帮你完成。"


async def get_mcp_tools() -> list[BaseTool]:
    """加载 Agent 可用的工具列表。

    加载策略:
    1. local_fallback: 直接使用本地工具
    2. mcp_remote: 强制从 MCP Server 拉取工具
    3. hybrid_debug: 优先 MCP，失败时回退本地工具

    返回:
        可供 LLM bind 的工具对象列表。
    """
    settings = get_settings()
    mode = _agent_tool_mode()

    if mode == "local_fallback":
        return _fallback_tools()

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except Exception:
        # 开发态允许依赖缺失时直接退回本地工具，生产态则显式报错
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
        # MCP 服务返回空工具列表时，开发态允许回退，本地排查更方便
        if mode == "hybrid_debug":
            return _fallback_tools()
        raise RuntimeError("MCP server returned no tools")
    except Exception:
        # 仅在 hybrid_debug 下允许 MCP 失败后继续跑本地工具
        if mode == "hybrid_debug":
            return _fallback_tools()
        raise


def _build_tool_lookup(tools: list[BaseTool]) -> dict[str, BaseTool]:
    """按工具名构建查找表，便于 tool_call 快速分发。"""
    return {getattr(t, "name", ""): t for t in tools if getattr(t, "name", None)}


async def create_runtime() -> dict[str, Any]:
    """创建并缓存 Agent 运行时。

    运行时包含:
    - 已绑定工具的流式 ChatOpenAI 实例
    - 原始工具列表
    - 工具名到工具对象的查找表
    - 当前模型名

    返回:
        可被 graph 节点复用的运行时字典。
    """
    global _runtime_cache
    if _runtime_cache is not None:
        return _runtime_cache

    async with _runtime_lock:
        if _runtime_cache is not None:
            return _runtime_cache

        try:
            from langchain_openai import ChatOpenAI
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("langchain-openai is required for agent llm node") from exc

        api_key = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        model = os.getenv("QWEN_CHAT_MODEL", "qwen3-32b")

        # 当前默认走兼容 OpenAI 协议的 Qwen / DashScope 接口
        llm = ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=0.2,
            streaming=True,
            extra_body={"enable_thinking": False},
        )
        tools = await get_mcp_tools()
        _runtime_cache = {
            "llm": llm.bind_tools(tools),
            "tools": tools,
            "tool_lookup": _build_tool_lookup(tools),
            "model": model,
        }
        return _runtime_cache


async def llm_node(state: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    """执行 LLM 决策节点。

    主要职责:
    1. 组装 system prompt 与当前对话消息
    2. 注入 memory retrieval 生成的补充上下文
    3. 调用 LLM，让其决定直接回复还是发起 tool_calls

    参数:
        state: 当前 AgentState。
        runtime: 由 create_runtime 构建的运行时对象。

    返回:
        包含新增 AIMessage 的状态增量。
    """
    llm = runtime["llm"]
    messages = list(state.get("messages", []))

    if not messages:
        return {
            "messages": [AIMessage(content="请先告诉我你想记录或查询什么奶茶信息。")],
        }

    first = messages[0]
    has_system = isinstance(first, tuple) and len(first) >= 1 and first[0] == "system"
    base_messages = list(messages)
    context = get_agent_context() or {}
    thread_id = str(context.get("thread_id") or "")
    try:
        user_id = _current_identity(state.get("user_id"))
    except PermissionError:
        user_id = ""
    memory_bundle: dict[str, Any] = {"prompts": [], "diagnostics": {}, "context_version": ""}
    system_prompt = ""
    system_prompt_version = ""
    if thread_id and user_id:
        prompt_bundle = await asyncio.to_thread(build_prompt_bundle, user_id=user_id, thread_id=thread_id, messages=messages)
        memory_bundle = dict(prompt_bundle.get("memory_bundle") or {})
        system_prompt = str(prompt_bundle.get("system_prompt") or "")
        system_prompt_version = str(prompt_bundle.get("system_prompt_version") or "")
    memory_messages = list(memory_bundle.get("prompts") or [])
    diagnostics = dict(memory_bundle.get("diagnostics") or {})
    if diagnostics:
        # 记录记忆上下文预算，用于观察检索注入是否过长或被截断
        logger.info(
            json.dumps(
                {
                    "event": "agent_memory_context_budget",
                    "thread_id": thread_id,
                    "prompt_count": diagnostics.get("prompt_count"),
                    "char_count": diagnostics.get("char_count"),
                    "estimated_tokens": diagnostics.get("estimated_tokens"),
                    "truncated": diagnostics.get("truncated"),
                },
                ensure_ascii=False,
                default=str,
            )
        )
        logger.info(
            json.dumps(
                {
                    "event": "agent_prompt_versions",
                    "thread_id": thread_id,
                    "system_prompt_version": system_prompt_version or get_settings().agent_system_prompt_version,
                    "context_version": memory_bundle.get("context_version") or get_settings().agent_memory_context_version,
                },
                ensure_ascii=False,
                default=str,
            )
        )

    if has_system:
        prompt_messages = [base_messages[0], *memory_messages, *base_messages[1:]]
    else:
        prompt_messages = [("system", system_prompt or "你是 Bobo 奶茶智能助手。"), *memory_messages, *base_messages]

    async def _call_llm():
        return await llm.ainvoke(prompt_messages)

    try:
        response = await call_with_resilience(
            "llm.chat",
            _call_llm,
            timeout_seconds=get_settings().llm_request_timeout_seconds,
            breaker=_dependency_breaker("llm.chat"),
        )
    except DependencyError as exc:
        logger.warning(
            json.dumps(
                {
                    "event": "agent_llm_degraded",
                    "category": exc.category,
                    "detail": str(exc),
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return {"messages": [AIMessage(content=_llm_degraded_reply(state, exc))]}
    return {"messages": [response]}


async def _invoke_tool(tool_obj: BaseTool, args: dict[str, Any]) -> Any:
    """兼容不同工具接口形态并执行工具。

    优先级:
    1. ainvoke
    2. invoke
    3. func

    参数:
        tool_obj: LangChain 工具对象。
        args: 工具参数。

    返回:
        工具执行结果。
    """
    tool_name = getattr(tool_obj, "name", "unknown")
    maybe_ainvoke = getattr(tool_obj, "ainvoke", None)
    if callable(maybe_ainvoke):
        return await asyncio.wait_for(maybe_ainvoke(args), timeout=_tool_timeout_seconds(tool_name))

    maybe_invoke = getattr(tool_obj, "invoke", None)
    if callable(maybe_invoke):
        result = await asyncio.wait_for(asyncio.to_thread(maybe_invoke, args), timeout=_tool_timeout_seconds(tool_name))
        if isinstance(result, Awaitable):
            return await asyncio.wait_for(result, timeout=_tool_timeout_seconds(tool_name))
        return result

    fn = getattr(tool_obj, "func", None)
    if callable(fn):
        result = await asyncio.wait_for(asyncio.to_thread(fn, **args), timeout=_tool_timeout_seconds(tool_name))
        if isinstance(result, Awaitable):
            return await asyncio.wait_for(result, timeout=_tool_timeout_seconds(tool_name))
        return result

    raise RuntimeError(f"tool {getattr(tool_obj, 'name', '<unknown>')} is not invokable")


def _with_tool_context(args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """为工具调用补齐上下文字段。

    除工具原始参数外，还会补充 user_id / request_id / thread_id / source，
    这样下游 tooling 层与审计日志能获得统一链路信息。

    返回:
        带上下文信息的工具参数字典。
    """
    context = get_agent_context() or {}
    payload = dict(args)
    payload["user_id"] = _current_identity(state.get("user_id"))
    payload["request_id"] = context.get("request_id") or state.get("request_id")
    payload["thread_id"] = context.get("thread_id")
    payload["source"] = context.get("source", "agent")
    return payload


async def tool_node(state: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    """执行工具节点。

    执行流程:
    1. 检查剩余步数
    2. 读取上一条 AIMessage 中的 tool_calls
    3. 逐个执行工具并记录审计日志
    4. 将工具结果包装成 ToolMessage 返回给下一轮 LLM

    参数:
        state: 当前 AgentState。
        runtime: Agent 运行时，包含工具查找表。

    返回:
        包含 ToolMessage、最新 tool_result 和递减后 max_steps 的状态增量。
    """
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
        # LangChain tool_call 中包含工具名、参数和调用 ID
        name = call.get("name")
        args = call.get("args") or {}
        tool_id = call.get("id") or ""
        contextualized_args = _with_tool_context(args, state)

        tool_obj = tool_lookup.get(name)
        if tool_obj is None:
            # 未知工具也要返回结构化错误，避免 LLM 无法理解失败原因
            payload = {
                "ok": False,
                "error": f"unknown_tool:{name}",
                "error_category": "unknown_tool",
                "error_type": "unknown_tool",
                "retryable": False,
                "dependency": f"tool:{name}",
            }
        else:
            try:
                validated_args = validate_tool_args(name or "unknown", contextualized_args)
                # 工具执行前后都写审计事件，便于后续排查 Agent 行为
                audit_tool_event(
                    name or "unknown",
                    "invoke_start",
                    user_id=contextualized_args["user_id"],
                    request_id=contextualized_args.get("request_id"),
                    thread_id=contextualized_args.get("thread_id"),
                    source=contextualized_args.get("source"),
                    args=list(validated_args.keys()),
                )
                data = await _invoke_tool(tool_obj, validated_args)
                payload = validate_tool_result(name or "unknown", data if isinstance(data, dict) else {"result": data})
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
                payload = build_tool_error_payload(name or "unknown", exc)

        latest_result = payload
        tool_messages.append(
            ToolMessage(
                # ToolMessage 需要文本 content，因此统一转为 JSON 字符串
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
    """根据 LLM 输出决定下一跳。

    - 有 tool_calls: 进入 tool_node
    - 无 tool_calls 或步数耗尽: 结束
    """
    if int(state.get("max_steps", 10)) <= 0:
        return "end"

    messages = state.get("messages", [])
    if not messages:
        return "end"

    last = messages[-1]
    tool_calls = getattr(last, "tool_calls", None) or []
    return "tool" if tool_calls else "end"


def route_after_tool(state: dict[str, Any]) -> str:
    """工具执行后决定是回到 LLM 还是直接结束。"""
    return "end" if int(state.get("max_steps", 10)) <= 0 else "llm"
