from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.models.db import get_pool, has_pool, query_stats

_LOCAL_THREADS: dict[str, dict[str, dict[str, Any]]] = {}
_LOCAL_MESSAGES: dict[str, list[dict[str, Any]]] = {}
_LOCAL_SUMMARIES: dict[str, list[dict[str, Any]]] = {}
_LOCAL_PROFILES: dict[str, dict[str, Any]] = {}
_LOCAL_MEMORIES: dict[str, list[dict[str, Any]]] = {}
_LOCAL_JOBS: list[dict[str, Any]] = []
_LOCAL_DAILY_LLM_USAGE: dict[tuple[str, str, str], dict[str, Any]] = {}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _profile_defaults(user_id: str) -> dict[str, Any]:
    now = _utcnow()
    return {
        "user_id": user_id,
        "profile_version": 1,
        "display_preferences": {},
        "drink_preferences": {},
        "interaction_preferences": {},
        "budget_preferences": {},
        "health_preferences": {},
        "memory_updated_at": now,
        "created_at": now,
        "updated_at": now,
    }


def _json_value(value: Any) -> Jsonb:
    return Jsonb(value if value is not None else {})


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        elif value is not None:
            merged[key] = deepcopy(value)
    return merged


def _fact_matches(
    item: dict[str, Any],
    normalized_fact: dict[str, Any] | None,
    *,
    scope: str | None = None,
    memory_type: str | None = None,
    include_inactive: bool = False,
) -> bool:
    if normalized_fact is None:
        return False
    if not include_inactive and item.get("status") != "active":
        return False
    if scope is not None and item.get("scope") != scope:
        return False
    if memory_type is not None and item.get("memory_type") != memory_type:
        return False
    return item.get("normalized_fact") == normalized_fact


def _normalize_thread_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["thread_id"] = payload.get("thread_key")
    return payload


def create_thread(user_id: str, thread_key: str, title: str | None = None) -> dict[str, Any]:
    existing = get_thread_by_key(user_id, thread_key)
    if existing:
        return existing

    now = _utcnow()
    if not has_pool():
        row = {
            "id": _new_id("thread"),
            "user_id": user_id,
            "thread_key": thread_key,
            "title": title,
            "status": "active",
            "message_count": 0,
            "last_user_message_at": None,
            "last_agent_message_at": None,
            "last_summary_at": None,
            "created_at": now,
            "updated_at": now,
            "archived_at": None,
        }
        _LOCAL_THREADS.setdefault(user_id, {})[thread_key] = row
        return _normalize_thread_row(row)

    sql = """
    INSERT INTO agent_threads (user_id, thread_key, title)
    VALUES (%s, %s, %s)
    ON CONFLICT (thread_key) DO UPDATE SET
      title = COALESCE(EXCLUDED.title, agent_threads.title),
      updated_at = NOW()
    RETURNING id::text, user_id::text AS user_id, thread_key, title, status, message_count,
              last_user_message_at, last_agent_message_at, last_summary_at, created_at, updated_at, archived_at
    """
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id, thread_key, title))
        row = cur.fetchone()
        conn.commit()
    return _normalize_thread_row(row or {})


def list_threads(user_id: str) -> list[dict[str, Any]]:
    if not has_pool():
        rows = list(_LOCAL_THREADS.get(user_id, {}).values())
        rows.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or _utcnow(), reverse=True)
        return [_normalize_thread_row(item) for item in rows]

    sql = """
    SELECT id::text, user_id::text AS user_id, thread_key, title, status, message_count,
           last_user_message_at, last_agent_message_at, last_summary_at, created_at, updated_at, archived_at
    FROM agent_threads
    WHERE user_id = %s
    ORDER BY updated_at DESC
    """
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id,))
        rows = cur.fetchall()
    return [_normalize_thread_row(row) for row in rows]


def get_thread_by_key(user_id: str, thread_key: str) -> dict[str, Any] | None:
    if not has_pool():
        row = _LOCAL_THREADS.get(user_id, {}).get(thread_key)
        return _normalize_thread_row(row) if row else None

    sql = """
    SELECT id::text, user_id::text AS user_id, thread_key, title, status, message_count,
           last_user_message_at, last_agent_message_at, last_summary_at, created_at, updated_at, archived_at
    FROM agent_threads
    WHERE user_id = %s AND thread_key = %s
    """
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id, thread_key))
        row = cur.fetchone()
    return _normalize_thread_row(row) if row else None


def archive_thread(user_id: str, thread_key: str) -> dict[str, Any] | None:
    if not has_pool():
        row = _LOCAL_THREADS.get(user_id, {}).get(thread_key)
        if not row:
            return None
        row["status"] = "archived"
        row["archived_at"] = _utcnow()
        row["updated_at"] = _utcnow()
        return _normalize_thread_row(row)

    sql = """
    UPDATE agent_threads
    SET status = 'archived', archived_at = NOW(), updated_at = NOW()
    WHERE user_id = %s AND thread_key = %s
    RETURNING id::text, user_id::text AS user_id, thread_key, title, status, message_count,
              last_user_message_at, last_agent_message_at, last_summary_at, created_at, updated_at, archived_at
    """
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id, thread_key))
        row = cur.fetchone()
        conn.commit()
    return _normalize_thread_row(row) if row else None


def clear_thread(user_id: str, thread_key: str) -> dict[str, Any] | None:
    thread = get_thread_by_key(user_id, thread_key)
    if not thread:
        return None

    if not has_pool():
        thread_id = str(thread["id"])
        _LOCAL_MESSAGES[thread_id] = []
        _LOCAL_SUMMARIES[thread_id] = []
        row = _LOCAL_THREADS[user_id][thread_key]
        row["message_count"] = 0
        row["last_summary_at"] = None
        row["last_user_message_at"] = None
        row["last_agent_message_at"] = None
        row["updated_at"] = _utcnow()
        return _normalize_thread_row(row)

    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM agent_thread_messages WHERE thread_id = %s::uuid", (thread["id"],))
        cur.execute("DELETE FROM agent_thread_summaries WHERE thread_id = %s::uuid", (thread["id"],))
        cur.execute(
            """
            UPDATE agent_threads
            SET message_count = 0,
                last_summary_at = NULL,
                last_user_message_at = NULL,
                last_agent_message_at = NULL,
                updated_at = NOW()
            WHERE id = %s::uuid
            RETURNING id::text, user_id::text AS user_id, thread_key, title, status, message_count,
                      last_user_message_at, last_agent_message_at, last_summary_at, created_at, updated_at, archived_at
            """,
            (thread["id"],),
        )
        row = cur.fetchone()
        conn.commit()
    return _normalize_thread_row(row) if row else None


def append_message(
    *,
    user_id: str,
    thread_key: str,
    role: str,
    content: str,
    content_type: str = "text",
    request_id: str | None = None,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    source: str = "agent",
) -> dict[str, Any]:
    thread = create_thread(user_id, thread_key)
    now = _utcnow()
    if not has_pool():
        row = {
            "id": _new_id("msg"),
            "thread_id": thread["id"],
            "user_id": user_id,
            "role": role,
            "content": content,
            "content_type": content_type,
            "request_id": request_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "source": source,
            "created_at": now,
        }
        _LOCAL_MESSAGES.setdefault(str(thread["id"]), []).append(row)
        thread_row = _LOCAL_THREADS[user_id][thread_key]
        thread_row["message_count"] += 1
        thread_row["updated_at"] = now
        if role == "user":
            thread_row["last_user_message_at"] = now
        elif role == "assistant":
            thread_row["last_agent_message_at"] = now
        return row

    sql = """
    INSERT INTO agent_thread_messages (
      thread_id, user_id, role, content, content_type, request_id, tool_name, tool_call_id, source
    ) VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id::text, thread_id::text AS thread_id, user_id::text AS user_id, role, content,
              content_type, request_id, tool_name, tool_call_id, source, created_at
    """
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (thread["id"], user_id, role, content, content_type, request_id, tool_name, tool_call_id, source),
        )
        row = cur.fetchone()
        if role == "user":
            cur.execute(
                """
                UPDATE agent_threads
                SET message_count = message_count + 1,
                    last_user_message_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s::uuid
                """,
                (thread["id"],),
            )
        elif role == "assistant":
            cur.execute(
                """
                UPDATE agent_threads
                SET message_count = message_count + 1,
                    last_agent_message_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s::uuid
                """,
                (thread["id"],),
            )
        else:
            cur.execute(
                "UPDATE agent_threads SET updated_at = NOW() WHERE id = %s::uuid",
                (thread["id"],),
            )
        conn.commit()
    return row or {}


def list_messages(user_id: str, thread_key: str) -> list[dict[str, Any]]:
    thread = get_thread_by_key(user_id, thread_key)
    if not thread:
        return []

    if not has_pool():
        return list(_LOCAL_MESSAGES.get(str(thread["id"]), []))

    sql = """
    SELECT id::text, thread_id::text AS thread_id, user_id::text AS user_id, role, content, content_type,
           request_id, tool_name, tool_call_id, source, created_at
    FROM agent_thread_messages
    WHERE thread_id = %s::uuid AND user_id = %s::uuid
    ORDER BY created_at ASC
    """
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (thread["id"], user_id))
        return cur.fetchall()


def list_recent_user_messages(user_id: str, thread_key: str, limit: int = 10) -> list[dict[str, Any]]:
    thread = get_thread_by_key(user_id, thread_key)
    if not thread:
        return []

    limit = max(int(limit or 0), 0)
    if limit == 0:
        return []

    if not has_pool():
        messages = [msg for msg in _LOCAL_MESSAGES.get(str(thread["id"]), []) if msg.get("role") == "user"]
        return deepcopy(messages[-limit:][::-1])

    sql = """
    SELECT id::text, thread_id::text AS thread_id, user_id::text AS user_id, role, content, content_type,
           request_id, tool_name, tool_call_id, source, created_at
    FROM agent_thread_messages
    WHERE thread_id = %s::uuid AND user_id = %s::uuid AND role = 'user'
    ORDER BY created_at DESC
    LIMIT %s
    """
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (thread["id"], user_id, limit))
        return cur.fetchall()


def save_summary(
    *,
    user_id: str,
    thread_key: str,
    summary_type: str,
    summary_text: str,
    open_slots: list[Any],
    covered_message_count: int,
    token_estimate: int | None = None,
) -> dict[str, Any]:
    thread = create_thread(user_id, thread_key)
    now = _utcnow()
    if not has_pool():
        row = {
            "id": _new_id("summary"),
            "thread_id": thread["id"],
            "user_id": user_id,
            "summary_type": summary_type,
            "summary_text": summary_text,
            "open_slots": list(open_slots),
            "covered_message_count": covered_message_count,
            "token_estimate": token_estimate,
            "created_at": now,
        }
        _LOCAL_SUMMARIES.setdefault(str(thread["id"]), []).append(row)
        _LOCAL_THREADS[user_id][thread_key]["last_summary_at"] = now
        return row

    sql = """
    INSERT INTO agent_thread_summaries (thread_id, user_id, summary_type, summary_text, open_slots, covered_message_count, token_estimate)
    VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s)
    RETURNING id::text, thread_id::text AS thread_id, user_id::text AS user_id, summary_type, summary_text,
              open_slots, covered_message_count, token_estimate, created_at
    """
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (thread["id"], user_id, summary_type, summary_text, Jsonb(open_slots), covered_message_count, token_estimate),
        )
        row = cur.fetchone()
        cur.execute("UPDATE agent_threads SET last_summary_at = NOW(), updated_at = NOW() WHERE id = %s::uuid", (thread["id"],))
        conn.commit()
    return row or {}


def get_daily_llm_usage(user_id: str, usage_date: date, model: str) -> dict[str, Any]:
    usage_day = usage_date.isoformat()
    if not has_pool():
        return deepcopy(
            _LOCAL_DAILY_LLM_USAGE.get(
                (user_id, usage_day, model),
                {
                    "user_id": user_id,
                    "usage_date": usage_day,
                    "model": model,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "estimated_cost_cny": 0.0,
                },
            )
        )

    sql = """
    SELECT user_id::text AS user_id, usage_date, model, input_tokens, output_tokens,
           estimated_cost_cny::float8 AS estimated_cost_cny, created_at, updated_at
    FROM user_daily_llm_usage
    WHERE user_id = %s::uuid AND usage_date = %s AND model = %s
    """
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id, usage_day, model))
        row = cur.fetchone()
    if row:
        return row
    return {
        "user_id": user_id,
        "usage_date": usage_day,
        "model": model,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_cny": 0.0,
    }


def add_daily_llm_usage(
    *,
    user_id: str,
    usage_date: date,
    model: str,
    input_tokens: int,
    output_tokens: int,
    estimated_cost_cny: float,
) -> dict[str, Any]:
    usage_day = usage_date.isoformat()
    if not has_pool():
        key = (user_id, usage_day, model)
        current = deepcopy(
            _LOCAL_DAILY_LLM_USAGE.get(
                key,
                {
                    "user_id": user_id,
                    "usage_date": usage_day,
                    "model": model,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "estimated_cost_cny": 0.0,
                },
            )
        )
        current["input_tokens"] = int(current.get("input_tokens") or 0) + max(int(input_tokens or 0), 0)
        current["output_tokens"] = int(current.get("output_tokens") or 0) + max(int(output_tokens or 0), 0)
        current["estimated_cost_cny"] = round(float(current.get("estimated_cost_cny") or 0.0) + max(float(estimated_cost_cny or 0.0), 0.0), 6)
        _LOCAL_DAILY_LLM_USAGE[key] = current
        return deepcopy(current)

    sql = """
    INSERT INTO user_daily_llm_usage (user_id, usage_date, model, input_tokens, output_tokens, estimated_cost_cny)
    VALUES (%s::uuid, %s, %s, %s, %s, %s)
    ON CONFLICT (user_id, usage_date, model) DO UPDATE SET
      input_tokens = user_daily_llm_usage.input_tokens + EXCLUDED.input_tokens,
      output_tokens = user_daily_llm_usage.output_tokens + EXCLUDED.output_tokens,
      estimated_cost_cny = user_daily_llm_usage.estimated_cost_cny + EXCLUDED.estimated_cost_cny,
      updated_at = NOW()
    RETURNING user_id::text AS user_id, usage_date, model, input_tokens, output_tokens,
              estimated_cost_cny::float8 AS estimated_cost_cny, created_at, updated_at
    """
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (user_id, usage_day, model, max(int(input_tokens or 0), 0), max(int(output_tokens or 0), 0), max(float(estimated_cost_cny or 0.0), 0.0)),
        )
        row = cur.fetchone()
        conn.commit()
    return row or {}


def latest_summary(user_id: str, thread_key: str) -> dict[str, Any] | None:
    thread = get_thread_by_key(user_id, thread_key)
    if not thread:
        return None
    if not has_pool():
        items = _LOCAL_SUMMARIES.get(str(thread["id"]), [])
        return items[-1] if items else None

    sql = """
    SELECT id::text, thread_id::text AS thread_id, user_id::text AS user_id, summary_type, summary_text,
           open_slots, covered_message_count, token_estimate, created_at
    FROM agent_thread_summaries
    WHERE thread_id = %s::uuid AND user_id = %s::uuid
    ORDER BY created_at DESC
    LIMIT 1
    """
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (thread["id"], user_id))
        return cur.fetchone()


def get_profile(user_id: str) -> dict[str, Any]:
    if not has_pool():
        return deepcopy(_LOCAL_PROFILES.setdefault(user_id, _profile_defaults(user_id)))

    sql = """
    SELECT user_id::text AS user_id, profile_version, display_preferences, drink_preferences,
           interaction_preferences, budget_preferences, health_preferences,
           memory_updated_at, created_at, updated_at
    FROM user_memory_profile
    WHERE user_id = %s::uuid
    """
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id,))
        row = cur.fetchone()
        if row:
            return row

        defaults = _profile_defaults(user_id)
        cur.execute(
            """
            INSERT INTO user_memory_profile (
              user_id, profile_version, display_preferences, drink_preferences,
              interaction_preferences, budget_preferences, health_preferences
            ) VALUES (%s::uuid, 1, %s, %s, %s, %s, %s)
            RETURNING user_id::text AS user_id, profile_version, display_preferences, drink_preferences,
                      interaction_preferences, budget_preferences, health_preferences,
                      memory_updated_at, created_at, updated_at
            """,
            (
                user_id,
                _json_value(defaults["display_preferences"]),
                _json_value(defaults["drink_preferences"]),
                _json_value(defaults["interaction_preferences"]),
                _json_value(defaults["budget_preferences"]),
                _json_value(defaults["health_preferences"]),
            ),
        )
        created = cur.fetchone()
        conn.commit()
    return created or defaults


def patch_profile(user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    current = get_profile(user_id)
    merged = _deep_merge(current, patch)
    merged["updated_at"] = _utcnow()
    merged["memory_updated_at"] = merged["updated_at"]
    merged["profile_version"] = int(current.get("profile_version") or 1) + 1

    if not has_pool():
        _LOCAL_PROFILES[user_id] = merged
        return deepcopy(merged)

    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE user_memory_profile
            SET profile_version = %s,
                display_preferences = %s,
                drink_preferences = %s,
                interaction_preferences = %s,
                budget_preferences = %s,
                health_preferences = %s,
                memory_updated_at = NOW(),
                updated_at = NOW()
            WHERE user_id = %s::uuid
            RETURNING user_id::text AS user_id, profile_version, display_preferences, drink_preferences,
                      interaction_preferences, budget_preferences, health_preferences,
                      memory_updated_at, created_at, updated_at
            """,
            (
                merged["profile_version"],
                _json_value(merged.get("display_preferences")),
                _json_value(merged.get("drink_preferences")),
                _json_value(merged.get("interaction_preferences")),
                _json_value(merged.get("budget_preferences")),
                _json_value(merged.get("health_preferences")),
                user_id,
            ),
        )
        row = cur.fetchone()
        conn.commit()
    return row or merged


def reset_profile(user_id: str) -> dict[str, Any]:
    defaults = _profile_defaults(user_id)
    if not has_pool():
        _LOCAL_PROFILES[user_id] = defaults
        return deepcopy(defaults)

    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE user_memory_profile
            SET profile_version = 1,
                display_preferences = '{}'::jsonb,
                drink_preferences = '{}'::jsonb,
                interaction_preferences = '{}'::jsonb,
                budget_preferences = '{}'::jsonb,
                health_preferences = '{}'::jsonb,
                memory_updated_at = NOW(),
                updated_at = NOW()
            WHERE user_id = %s::uuid
            RETURNING user_id::text AS user_id, profile_version, display_preferences, drink_preferences,
                      interaction_preferences, budget_preferences, health_preferences,
                      memory_updated_at, created_at, updated_at
            """,
            (user_id,),
        )
        row = cur.fetchone()
        conn.commit()
    return row or defaults


def list_memories(user_id: str, include_inactive: bool = False) -> list[dict[str, Any]]:
    if not has_pool():
        items = deepcopy(_LOCAL_MEMORIES.get(user_id, []))
        if include_inactive:
            return items
        return [item for item in items if item.get("status") == "active"]

    sql = """
    SELECT id::text, user_id::text AS user_id, memory_type, scope, content, normalized_fact, source_kind,
           source_ref, confidence::float8 AS confidence, salience::float8 AS salience, status, expires_at,
           created_at, updated_at, last_used_at
    FROM user_memory_items
    WHERE user_id = %s::uuid
    """
    if not include_inactive:
        sql += " AND status = 'active'"
    sql += " ORDER BY updated_at DESC, created_at DESC"
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id,))
        return cur.fetchall()


def find_similar_memory_by_fact(
    user_id: str,
    normalized_fact: dict[str, Any] | None,
    *,
    scope: str | None = None,
    memory_type: str | None = None,
    include_inactive: bool = False,
) -> dict[str, Any] | None:
    if normalized_fact is None:
        return None

    if not has_pool():
        items = list_memories(user_id, include_inactive=True)
        for item in items:
            if _fact_matches(
                item,
                normalized_fact,
                scope=scope,
                memory_type=memory_type,
                include_inactive=include_inactive,
            ):
                return deepcopy(item)
        return None

    where = [
        "user_id = %s::uuid",
        "normalized_fact = %s::jsonb",
    ]
    params: list[Any] = [user_id, Jsonb(normalized_fact)]
    if scope is not None:
        where.append("scope = %s")
        params.append(scope)
    if memory_type is not None:
        where.append("memory_type = %s")
        params.append(memory_type)
    if not include_inactive:
        where.append("status = 'active'")

    sql = f"""
    SELECT id::text, user_id::text AS user_id, memory_type, scope, content, normalized_fact, source_kind,
           source_ref, confidence::float8 AS confidence, salience::float8 AS salience, status, expires_at,
           created_at, updated_at, last_used_at
    FROM user_memory_items
    WHERE {' AND '.join(where)}
    ORDER BY updated_at DESC, created_at DESC
    LIMIT 1
    """
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return cur.fetchone()


def create_memory_item(
    *,
    user_id: str,
    memory_type: str,
    scope: str,
    content: str,
    normalized_fact: dict[str, Any] | None,
    source_kind: str,
    source_ref: str | None,
    confidence: float = 0.5,
    salience: float = 0.5,
    status: str = "active",
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    now = _utcnow()
    row = {
        "id": _new_id("memory"),
        "user_id": user_id,
        "memory_type": memory_type,
        "scope": scope,
        "content": content,
        "normalized_fact": normalized_fact,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "confidence": confidence,
        "salience": salience,
        "status": status,
        "expires_at": expires_at,
        "created_at": now,
        "updated_at": now,
        "last_used_at": None,
    }
    if not has_pool():
        _LOCAL_MEMORIES.setdefault(user_id, []).append(row)
        return deepcopy(row)

    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_memory_items (
              user_id, memory_type, scope, content, normalized_fact, source_kind, source_ref,
              confidence, salience, status, expires_at
            ) VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id::text, user_id::text AS user_id, memory_type, scope, content, normalized_fact,
                      source_kind, source_ref, confidence::float8 AS confidence, salience::float8 AS salience,
                      status, expires_at, created_at, updated_at, last_used_at
            """,
            (
                user_id,
                memory_type,
                scope,
                content,
                Jsonb(normalized_fact) if normalized_fact is not None else None,
                source_kind,
                source_ref,
                confidence,
                salience,
                status,
                expires_at,
            ),
        )
        created = cur.fetchone()
        conn.commit()
    return created or row


def upsert_memory_item_by_fact(
    *,
    user_id: str,
    memory_type: str,
    scope: str,
    content: str,
    normalized_fact: dict[str, Any] | None,
    source_kind: str,
    source_ref: str | None,
    confidence: float = 0.5,
    salience: float = 0.5,
    status: str = "active",
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    if normalized_fact is None:
        return create_memory_item(
            user_id=user_id,
            memory_type=memory_type,
            scope=scope,
            content=content,
            normalized_fact=normalized_fact,
            source_kind=source_kind,
            source_ref=source_ref,
            confidence=confidence,
            salience=salience,
            status=status,
            expires_at=expires_at,
        )

    existing = find_similar_memory_by_fact(
        user_id,
        normalized_fact,
        scope=scope,
        memory_type=memory_type,
        include_inactive=True,
    )
    if existing is None:
        return create_memory_item(
            user_id=user_id,
            memory_type=memory_type,
            scope=scope,
            content=content,
            normalized_fact=normalized_fact,
            source_kind=source_kind,
            source_ref=source_ref,
            confidence=confidence,
            salience=salience,
            status=status,
            expires_at=expires_at,
        )

    now = _utcnow()
    updated_row = {
        **existing,
        "memory_type": memory_type,
        "scope": scope,
        "content": content,
        "normalized_fact": normalized_fact,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "confidence": confidence,
        "salience": salience,
        "status": status,
        "expires_at": expires_at,
        "updated_at": now,
    }

    if not has_pool():
        items = _LOCAL_MEMORIES.setdefault(user_id, [])
        for index, item in enumerate(items):
            if item["id"] == existing["id"]:
                items[index] = updated_row
                return deepcopy(updated_row)
        items.append(updated_row)
        return deepcopy(updated_row)

    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE user_memory_items
            SET memory_type = %s,
                scope = %s,
                content = %s,
                normalized_fact = %s,
                source_kind = %s,
                source_ref = %s,
                confidence = %s,
                salience = %s,
                status = %s,
                expires_at = %s,
                updated_at = NOW()
            WHERE user_id = %s::uuid AND id = %s::uuid
            RETURNING id::text, user_id::text AS user_id, memory_type, scope, content, normalized_fact,
                      source_kind, source_ref, confidence::float8 AS confidence, salience::float8 AS salience,
                      status, expires_at, created_at, updated_at, last_used_at
            """,
            (
                memory_type,
                scope,
                content,
                Jsonb(normalized_fact),
                source_kind,
                source_ref,
                confidence,
                salience,
                status,
                expires_at,
                user_id,
                existing["id"],
            ),
        )
        row = cur.fetchone()
        conn.commit()
    return row or updated_row


def disable_memory_item(user_id: str, memory_id: str) -> bool:
    return _update_memory_status(user_id, memory_id, "disabled")


def delete_memory_item(user_id: str, memory_id: str) -> bool:
    if not has_pool():
        items = _LOCAL_MEMORIES.get(user_id, [])
        before = len(items)
        _LOCAL_MEMORIES[user_id] = [item for item in items if item["id"] != memory_id]
        return len(_LOCAL_MEMORIES[user_id]) != before

    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM user_memory_items WHERE user_id = %s::uuid AND id = %s::uuid", (user_id, memory_id))
        deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def _update_memory_status(user_id: str, memory_id: str, status: str) -> bool:
    if not has_pool():
        for item in _LOCAL_MEMORIES.get(user_id, []):
            if item["id"] == memory_id:
                item["status"] = status
                item["updated_at"] = _utcnow()
                return True
        return False

    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE user_memory_items SET status = %s, updated_at = NOW() WHERE user_id = %s::uuid AND id = %s::uuid",
            (status, user_id, memory_id),
        )
        updated = cur.rowcount > 0
        conn.commit()
    return updated


def touch_memory_item(user_id: str, memory_id: str) -> None:
    if not has_pool():
        for item in _LOCAL_MEMORIES.get(user_id, []):
            if item["id"] == memory_id:
                item["last_used_at"] = _utcnow()
                break
        return

    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE user_memory_items SET last_used_at = NOW(), updated_at = NOW() WHERE user_id = %s::uuid AND id = %s::uuid",
            (user_id, memory_id),
        )
        conn.commit()


def enqueue_job(user_id: str, job_type: str, payload: dict[str, Any], thread_key: str | None = None) -> dict[str, Any]:
    thread = get_thread_by_key(user_id, thread_key) if thread_key else None
    now = _utcnow()
    row = {
        "id": _new_id("job"),
        "user_id": user_id,
        "thread_id": thread["id"] if thread else None,
        "thread_key": thread_key,
        "job_type": job_type,
        "payload": deepcopy(payload),
        "status": "pending",
        "attempt_count": 0,
        "last_error": None,
        "scheduled_at": now,
        "created_at": now,
        "updated_at": now,
    }
    if not has_pool():
        _LOCAL_JOBS.append(row)
        return deepcopy(row)

    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO memory_write_jobs (user_id, thread_id, job_type, payload)
            VALUES (%s::uuid, %s::uuid, %s, %s)
            RETURNING id::text, user_id::text AS user_id, thread_id::text AS thread_id, job_type, payload,
                      status, attempt_count, last_error, scheduled_at, created_at, updated_at
            """,
            (user_id, thread["id"] if thread else None, job_type, Jsonb(payload)),
        )
        created = cur.fetchone()
        conn.commit()
    if created and thread_key:
        created["thread_key"] = thread_key
    return created or row


def list_pending_jobs(limit: int = 20) -> list[dict[str, Any]]:
    if not has_pool():
        return [deepcopy(item) for item in _LOCAL_JOBS if item["status"] == "pending"][:limit]

    sql = """
    SELECT id::text, user_id::text AS user_id, thread_id::text AS thread_id, job_type, payload,
           status, attempt_count, last_error, scheduled_at, created_at, updated_at
    FROM memory_write_jobs
    WHERE status = 'pending'
    ORDER BY scheduled_at ASC
    LIMIT %s
    """
    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (limit,))
        return cur.fetchall()


def mark_job_status(job_id: str, status: str, *, attempt_count: int | None = None, last_error: str | None = None) -> None:
    if not has_pool():
        for item in _LOCAL_JOBS:
            if item["id"] == job_id:
                item["status"] = status
                item["updated_at"] = _utcnow()
                if attempt_count is not None:
                    item["attempt_count"] = attempt_count
                item["last_error"] = last_error
                break
        return

    pool = get_pool()
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE memory_write_jobs
            SET status = %s,
                attempt_count = COALESCE(%s, attempt_count),
                last_error = %s,
                updated_at = NOW()
            WHERE id = %s::uuid
            """,
            (status, attempt_count, last_error, job_id),
        )
        conn.commit()


def derive_profile_candidates_from_stats(user_id: str) -> dict[str, Any]:
    stats = query_stats(user_id, "all", None)
    drink_preferences: dict[str, Any] = {}
    budget_preferences: dict[str, Any] = {}

    sugar_pref = stats.get("sugar_pref") or []
    if sugar_pref:
        drink_preferences["default_sugar"] = sugar_pref[0].get("sugar")

    ice_pref = stats.get("ice_pref") or []
    if ice_pref:
        drink_preferences["default_ice"] = ice_pref[0].get("ice")

    brands = stats.get("brand_dist") or []
    if brands:
        drink_preferences["preferred_brands"] = [row.get("brand") for row in brands[:3] if row.get("brand")]

    total_amount = float(stats.get("total_amount") or 0)
    total_count = int(stats.get("total_count") or 0)
    if total_count:
        budget_preferences["soft_price_ceiling"] = round(total_amount / total_count, 1)
        budget_preferences["price_sensitive"] = total_count >= 5 and total_amount / total_count <= 22

    payload: dict[str, Any] = {}
    if drink_preferences:
        payload["drink_preferences"] = drink_preferences
    if budget_preferences:
        payload["budget_preferences"] = budget_preferences
    return payload
