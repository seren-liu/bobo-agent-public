from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.brands import canonicalize_brand_name
from app.core.config import get_settings, to_psycopg_conninfo
from app.memory.profile import get_profile
from app.models.db import insert_records, query_calendar, query_day, query_recent, query_stats
from app.services.menu_search import get_menu_search_service
from app.services.menu_ops import MenuActionError, get_menu_ops_service
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
    brand = canonicalize_brand_name(brand) or brand
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


def get_menu_brand_coverage_impl(*, brand: str | None = None, user_id: str | None = None, request_id: str | None = None, thread_id: str | None = None, source: str | None = None) -> bool | None:
    context = resolve_tool_context(
        user_id=user_id,
        request_id=request_id,
        thread_id=thread_id,
        source=source,
        required_user=False,
    )
    normalized_brand = canonicalize_brand_name(brand) if brand else None
    if not normalized_brand:
        return False

    database_url = get_settings().database_url
    if not database_url:
        return None
    database_url = to_psycopg_conninfo(database_url)

    audit_tool_event(
        "menu_brand_coverage",
        "start",
        user_id=context.get("user_id"),
        request_id=context.get("request_id"),
        thread_id=context.get("thread_id"),
        source=context.get("source"),
        brand=normalized_brand,
    )

    try:
        with psycopg.connect(database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM menu
                WHERE brand = %s AND is_active = TRUE
                LIMIT 1
                """,
                (normalized_brand,),
            )
            found = cur.fetchone() is not None
    except Exception:
        return None

    audit_tool_event(
        "menu_brand_coverage",
        "success",
        user_id=context.get("user_id"),
        request_id=context.get("request_id"),
        thread_id=context.get("thread_id"),
        source=context.get("source"),
        brand=normalized_brand,
        covered=found,
    )
    return found


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
    brand = canonicalize_brand_name(brand)
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
    results = await get_menu_search_service().search(
        query=query,
        brand=brand,
        top_k=5,
        source=context["source"],
    )
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


def get_recent_records_impl(
    *,
    limit: int = 5,
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
        "get_recent_records",
        "start",
        user_id=context["user_id"],
        request_id=context["request_id"],
        thread_id=context["thread_id"],
        source=context["source"],
        limit=limit,
    )
    payload = {"records": query_recent(context["user_id"], limit)}
    audit_tool_event(
        "get_recent_records",
        "success",
        user_id=context["user_id"],
        request_id=context["request_id"],
        thread_id=context["thread_id"],
        source=context["source"],
        result_count=len(payload["records"]),
    )
    return payload


def get_day_impl(
    *,
    date: str,
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
        "get_day",
        "start",
        user_id=context["user_id"],
        request_id=context["request_id"],
        thread_id=context["thread_id"],
        source=context["source"],
        date=date,
    )
    payload = query_day(context["user_id"], datetime.fromisoformat(date).date())
    audit_tool_event(
        "get_day",
        "success",
        user_id=context["user_id"],
        request_id=context["request_id"],
        thread_id=context["thread_id"],
        source=context["source"],
        record_count=len(payload.get("records") or []),
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
