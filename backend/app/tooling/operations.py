from __future__ import annotations

from datetime import datetime, timezone

from app.memory.profile import get_profile
from app.models.db import insert_records, query_calendar, query_stats
from app.services.menu_ops import MenuActionError, get_menu_ops_service
from app.services.qdrant import QdrantService
from app.tooling.context import audit_tool_event, resolve_tool_context


def record_drink_impl(
    *,
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
    context = resolve_tool_context(
        user_id=user_id,
        request_id=request_id,
        thread_id=thread_id,
        source=source,
        required_user=True,
    )
    ts = consumed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    audit_tool_event(
        "record_drink",
        "start",
        user_id=context["user_id"],
        request_id=context["request_id"],
        thread_id=context["thread_id"],
        source=context["source"],
        brand=brand,
        name=name,
    )
    profile = get_profile(context["user_id"])
    drink_preferences = profile.get("drink_preferences") or {}
    records = insert_records(
        context["user_id"],
        [
            {
                "menu_id": None,
                "brand": brand,
                "name": name,
                "size": None,
                "sugar": sugar or drink_preferences.get("default_sugar"),
                "ice": ice or drink_preferences.get("default_ice"),
                "mood": mood,
                "price": price,
                "photo_url": photo_url,
                "source": source or "agent",
                "notes": None,
                "consumed_at": ts,
            }
        ],
    )
    payload = {"ok": True, "records": records}
    audit_tool_event(
        "record_drink",
        "success",
        user_id=context["user_id"],
        request_id=context["request_id"],
        thread_id=context["thread_id"],
        source=context["source"],
        records=len(records),
    )
    return payload


async def search_menu_impl(
    *,
    query: str,
    brand: str | None = None,
    user_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    source: str | None = None,
) -> dict:
    context = resolve_tool_context(
        user_id=user_id,
        request_id=request_id,
        thread_id=thread_id,
        source=source,
        required_user=True,
    )
    audit_tool_event(
        "search_menu",
        "start",
        user_id=context["user_id"],
        request_id=context["request_id"],
        thread_id=context["thread_id"],
        source=context["source"],
        query=query,
        brand=brand,
    )
    service = QdrantService()
    results = await service.search(query=query, brand=brand, top_k=5)
    payload = {"results": results, "query": query, "brand": brand}
    audit_tool_event(
        "search_menu",
        "success",
        user_id=context["user_id"],
        request_id=context["request_id"],
        thread_id=context["thread_id"],
        source=context["source"],
        result_count=len(results),
    )
    return payload


def get_stats_impl(
    *,
    period: str = "month",
    date: str | None = None,
    user_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    source: str | None = None,
) -> dict:
    context = resolve_tool_context(
        user_id=user_id,
        request_id=request_id,
        thread_id=thread_id,
        source=source,
        required_user=True,
    )
    audit_tool_event(
        "get_stats",
        "start",
        user_id=context["user_id"],
        request_id=context["request_id"],
        thread_id=context["thread_id"],
        source=context["source"],
        period=period,
        date=date,
    )
    payload = query_stats(context["user_id"], period, date)
    audit_tool_event(
        "get_stats",
        "success",
        user_id=context["user_id"],
        request_id=context["request_id"],
        thread_id=context["thread_id"],
        source=context["source"],
    )
    return payload


def get_calendar_impl(
    *,
    year: int,
    month: int,
    user_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    source: str | None = None,
) -> dict:
    context = resolve_tool_context(
        user_id=user_id,
        request_id=request_id,
        thread_id=thread_id,
        source=source,
        required_user=True,
    )
    audit_tool_event(
        "get_calendar",
        "start",
        user_id=context["user_id"],
        request_id=context["request_id"],
        thread_id=context["thread_id"],
        source=context["source"],
        year=year,
        month=month,
    )
    payload = query_calendar(context["user_id"], year, month)
    audit_tool_event(
        "get_calendar",
        "success",
        user_id=context["user_id"],
        request_id=context["request_id"],
        thread_id=context["thread_id"],
        source=context["source"],
    )
    return payload


async def update_menu_impl(
    *,
    action: str,
    item: dict,
    user_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    source: str | None = None,
) -> dict:
    context = resolve_tool_context(
        user_id=user_id,
        request_id=request_id,
        thread_id=thread_id,
        source=source,
        required_user=True,
    )
    audit_tool_event(
        "update_menu",
        "start",
        user_id=context["user_id"],
        request_id=context["request_id"],
        thread_id=context["thread_id"],
        source=context["source"],
        action=action,
        menu_id=item.get("id") or item.get("menu_id"),
    )
    try:
        result = await get_menu_ops_service().apply_action(action=action, item=item)
    except MenuActionError as exc:
        audit_tool_event(
            "update_menu",
            "error",
            user_id=context["user_id"],
            request_id=context["request_id"],
            thread_id=context["thread_id"],
            source=context["source"],
            error=str(exc),
        )
        return {
            "ok": False,
            "action": action,
            "menu_id": str(item.get("id") or item.get("menu_id") or ""),
            "db_updated": False,
            "vector_updated": False,
            "warnings": [str(exc)],
        }
    audit_tool_event(
        "update_menu",
        "success",
        user_id=context["user_id"],
        request_id=context["request_id"],
        thread_id=context["thread_id"],
        source=context["source"],
        menu_id=result.get("menu_id"),
        db_updated=result.get("db_updated"),
        vector_updated=result.get("vector_updated"),
    )
    return result
