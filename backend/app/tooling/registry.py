from __future__ import annotations

from typing import Any

from app.core.authz import get_auth_context, has_capability
from app.tooling.operations import (
    get_calendar_impl,
    get_day_impl,
    get_recent_records_impl,
    get_stats_impl,
    record_drink_impl,
    search_menu_impl,
    update_menu_impl,
)
from app.tooling.context import resolve_tool_context

try:
    from langchain_core.tools import BaseTool, tool
except Exception:  # pragma: no cover
    BaseTool = Any

    def tool(*_args, **_kwargs):
        def _decorator(func):
            return func

        return _decorator


TOOL_NAMES = [
    "record_drink",
    "search_menu",
    "get_stats",
    "get_recent_records",
    "get_day",
    "get_calendar",
    "update_menu",
]


def guard_actor(user_id: str | None) -> str:
    auth_context = get_auth_context() or {}
    auth_user_id = str(auth_context.get("user_id") or "").strip()
    auth_source = str(auth_context.get("auth_source") or "").strip()
    requested_user_id = (user_id or "").strip()

    if auth_source == "user_bearer":
        if requested_user_id and requested_user_id != auth_user_id:
            raise PermissionError("mcp user token cannot act on behalf of another user")
        return resolve_tool_context(user_id=auth_user_id, required_user=True)["user_id"]

    if requested_user_id:
        return resolve_tool_context(user_id=requested_user_id, required_user=True)["user_id"]

    return resolve_tool_context(user_id=user_id, required_user=True)["user_id"]


def guard_capability(tool_name: str) -> None:
    auth_context = get_auth_context() or {}
    capabilities = tuple(auth_context.get("capabilities") or ())
    if "*" in capabilities:
        return
    if not has_capability(tool_name, capabilities):
        raise PermissionError(f"missing capability for tool:{tool_name}")


@tool("record_drink")
def record_drink_tool(
    brand: str,
    name: str,
    sugar: str | None = None,
    ice: str | None = None,
    mood: str | None = None,
    price: float | None = None,
    photo_url: str | None = None,
    consumed_at: str | None = None,
    user_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    source: str | None = None,
) -> dict:
    """记录一条饮品数据。"""
    return record_drink_impl(
        brand=brand,
        name=name,
        sugar=sugar,
        ice=ice,
        mood=mood,
        price=price,
        photo_url=photo_url,
        consumed_at=consumed_at,
        user_id=user_id,
        request_id=request_id,
        thread_id=thread_id,
        source=source,
    )


@tool("search_menu")
async def search_menu_tool(
    query: str,
    brand: str | None = None,
    user_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    source: str | None = None,
) -> dict:
    """按语义检索菜单。"""
    return await search_menu_impl(
        query=query,
        brand=brand,
        user_id=user_id,
        request_id=request_id,
        thread_id=thread_id,
        source=source,
    )


@tool("get_stats")
def get_stats_tool(
    period: str = "month",
    date: str | None = None,
    user_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    source: str | None = None,
) -> dict:
    """查询饮品统计。"""
    return get_stats_impl(
        period=period,
        date=date,
        user_id=user_id,
        request_id=request_id,
        thread_id=thread_id,
        source=source,
    )


@tool("get_recent_records")
def get_recent_records_tool(
    limit: int = 5,
    user_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    source: str | None = None,
) -> dict:
    """查询最近饮品记录。"""
    return get_recent_records_impl(
        limit=limit,
        user_id=user_id,
        request_id=request_id,
        thread_id=thread_id,
        source=source,
    )


@tool("get_day")
def get_day_tool(
    date: str,
    user_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    source: str | None = None,
) -> dict:
    """查询某一天的饮品记录。"""
    return get_day_impl(
        date=date,
        user_id=user_id,
        request_id=request_id,
        thread_id=thread_id,
        source=source,
    )


@tool("get_calendar")
def get_calendar_tool(
    year: int,
    month: int,
    user_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    source: str | None = None,
) -> dict:
    """查询月历摘要。"""
    return get_calendar_impl(
        year=year,
        month=month,
        user_id=user_id,
        request_id=request_id,
        thread_id=thread_id,
        source=source,
    )


@tool("update_menu")
async def update_menu_tool(
    action: str,
    item: dict,
    user_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    source: str | None = None,
) -> dict:
    """更新菜单（add/update/delete），并同步向量索引。"""
    return await update_menu_impl(
        action=action,
        item=item,
        user_id=user_id,
        request_id=request_id,
        thread_id=thread_id,
        source=source,
    )


def get_local_tools() -> list[BaseTool]:
    return [record_drink_tool, search_menu_tool, get_stats_tool, get_recent_records_tool, get_day_tool, get_calendar_tool, update_menu_tool]


def register_mcp_tools(mcp: Any) -> None:
    @mcp.tool(name="record_drink")
    def mcp_record_drink(
        brand: str,
        name: str,
        sugar: str | None = None,
        ice: str | None = None,
        mood: str | None = None,
        price: float | None = None,
        photo_url: str | None = None,
        consumed_at: str | None = None,
        user_id: str | None = None,
        request_id: str | None = None,
        thread_id: str | None = None,
        source: str | None = None,
    ) -> dict:
        guard_capability("record_drink")
        return record_drink_impl(
            brand=brand,
            name=name,
            sugar=sugar,
            ice=ice,
            mood=mood,
            price=price,
            photo_url=photo_url,
            consumed_at=consumed_at,
            user_id=guard_actor(user_id),
            request_id=request_id,
            thread_id=thread_id,
            source=source,
        )

    @mcp.tool(name="search_menu")
    async def mcp_search_menu(
        query: str,
        brand: str | None = None,
        user_id: str | None = None,
        request_id: str | None = None,
        thread_id: str | None = None,
        source: str | None = None,
    ) -> dict:
        guard_capability("search_menu")
        return await search_menu_impl(
            query=query,
            brand=brand,
            user_id=guard_actor(user_id),
            request_id=request_id,
            thread_id=thread_id,
            source=source,
        )

    @mcp.tool(name="get_stats")
    def mcp_get_stats(
        period: str = "month",
        date: str | None = None,
        user_id: str | None = None,
        request_id: str | None = None,
        thread_id: str | None = None,
        source: str | None = None,
    ) -> dict:
        guard_capability("get_stats")
        return get_stats_impl(
            period=period,
            date=date,
            user_id=guard_actor(user_id),
            request_id=request_id,
            thread_id=thread_id,
            source=source,
        )

    @mcp.tool(name="get_recent_records")
    def mcp_get_recent_records(
        limit: int = 5,
        user_id: str | None = None,
        request_id: str | None = None,
        thread_id: str | None = None,
        source: str | None = None,
    ) -> dict:
        guard_capability("get_recent_records")
        return get_recent_records_impl(
            limit=limit,
            user_id=guard_actor(user_id),
            request_id=request_id,
            thread_id=thread_id,
            source=source,
        )

    @mcp.tool(name="get_day")
    def mcp_get_day(
        date: str,
        user_id: str | None = None,
        request_id: str | None = None,
        thread_id: str | None = None,
        source: str | None = None,
    ) -> dict:
        guard_capability("get_day")
        return get_day_impl(
            date=date,
            user_id=guard_actor(user_id),
            request_id=request_id,
            thread_id=thread_id,
            source=source,
        )

    @mcp.tool(name="get_calendar")
    def mcp_get_calendar(
        year: int,
        month: int,
        user_id: str | None = None,
        request_id: str | None = None,
        thread_id: str | None = None,
        source: str | None = None,
    ) -> dict:
        guard_capability("get_calendar")
        return get_calendar_impl(
            year=year,
            month=month,
            user_id=guard_actor(user_id),
            request_id=request_id,
            thread_id=thread_id,
            source=source,
        )

    @mcp.tool(name="update_menu")
    async def mcp_update_menu(
        action: str,
        item: dict,
        user_id: str | None = None,
        request_id: str | None = None,
        thread_id: str | None = None,
        source: str | None = None,
    ) -> dict:
        guard_capability("update_menu")
        return await update_menu_impl(
            action=action,
            item=item,
            user_id=guard_actor(user_id),
            request_id=request_id,
            thread_id=thread_id,
            source=source,
        )
