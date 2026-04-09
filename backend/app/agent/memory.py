"""Agent 记忆与会话存储模块。

本模块负责 Agent 侧的会话线程、消息、摘要、长期记忆和记忆写入任务管理。
它既支持 PostgreSQL 持久化模式，也支持无数据库时的本地内存 fallback。

核心能力:
- 会话线程管理: 注册、查询、归档、清空线程
- 消息与摘要持久化: 保存对话消息和滚动摘要
- 用户画像管理: 读取、合并、更新 memory profile
- 长期记忆管理: 检索、写入、禁用、删除 memory item
- Prompt 上下文装配: 将画像、摘要、长期记忆与最近消息拼成模型输入
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.core.config import get_settings
from app.models import db as db_module

try:
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover
    dict_row = None


DEFAULT_SUMMARY_TRIGGER_COUNT = 20
DEFAULT_RECENT_WINDOW = 8
DEFAULT_SEMANTIC_TOP_K = 4

# 标记数据库 schema 是否已完成初始化，避免重复建表
_SCHEMA_READY = False

# 本地内存 fallback 存储。
# 当数据库连接池不可用时，Agent 仍可在单进程内完成最小功能闭环。
_LOCAL_THREADS: dict[str, dict[str, Any]] = {}
_LOCAL_THREADS_BY_KEY: dict[str, str] = {}
_LOCAL_MESSAGES: dict[str, list[dict[str, Any]]] = {}
_LOCAL_SUMMARIES: dict[str, list[dict[str, Any]]] = {}
_LOCAL_PROFILES: dict[str, dict[str, Any]] = {}
_LOCAL_MEMORIES: dict[str, dict[str, Any]] = {}
_LOCAL_JOBS: list[dict[str, Any]] = []


def _pool():
    """获取底层数据库连接池。

    返回:
        数据库连接池对象；如果当前未初始化数据库则返回 None。
    """
    return getattr(db_module, "_pool", None)


def _utc_now() -> datetime:
    """获取当前 UTC 时间。"""
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    """获取当前 UTC ISO 时间字符串。"""
    return _utc_now().isoformat().replace("+00:00", "Z")


def _new_id(prefix: str = "local") -> str:
    """生成本地 fallback 用的字符串 ID。"""
    return f"{prefix}-{uuid4().hex}"


def reset_local_state() -> None:
    """重置所有本地 fallback 状态。

    主要用于测试场景，确保内存态线程、消息、摘要和记忆不会相互污染。
    """
    _LOCAL_THREADS.clear()
    _LOCAL_THREADS_BY_KEY.clear()
    _LOCAL_MESSAGES.clear()
    _LOCAL_SUMMARIES.clear()
    _LOCAL_PROFILES.clear()
    _LOCAL_MEMORIES.clear()
    _LOCAL_JOBS.clear()


def _ensure_schema() -> None:
    """确保 Agent memory 相关数据库表存在。

    只有在数据库连接池可用且 schema 尚未初始化时才执行建表逻辑。
    本地 fallback 模式下会直接跳过。
    """
    global _SCHEMA_READY
    pool = _pool()
    if _SCHEMA_READY or pool is None:
        return

    # 统一在首次访问时懒初始化 schema，减少本地开发启动前置依赖。
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_threads (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              user_id UUID NOT NULL REFERENCES user_profile(user_id),
              thread_key VARCHAR(255) NOT NULL UNIQUE,
              title VARCHAR(120),
              status VARCHAR(24) NOT NULL DEFAULT 'active',
              message_count INT NOT NULL DEFAULT 0,
              last_user_message_at TIMESTAMPTZ,
              last_agent_message_at TIMESTAMPTZ,
              last_summary_at TIMESTAMPTZ,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              archived_at TIMESTAMPTZ
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_threads_user_updated_at ON agent_threads (user_id, updated_at DESC)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_thread_messages (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              thread_id UUID NOT NULL REFERENCES agent_threads(id) ON DELETE CASCADE,
              user_id UUID NOT NULL REFERENCES user_profile(user_id),
              role VARCHAR(24) NOT NULL,
              content TEXT NOT NULL,
              content_type VARCHAR(24) NOT NULL DEFAULT 'text',
              request_id VARCHAR(64),
              tool_name VARCHAR(80),
              tool_call_id VARCHAR(120),
              source VARCHAR(24) NOT NULL DEFAULT 'agent',
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_thread_messages_thread_created_at ON agent_thread_messages (thread_id, created_at ASC)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_thread_summaries (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              thread_id UUID NOT NULL REFERENCES agent_threads(id) ON DELETE CASCADE,
              user_id UUID NOT NULL REFERENCES user_profile(user_id),
              summary_type VARCHAR(24) NOT NULL,
              summary_text TEXT NOT NULL,
              open_slots JSONB NOT NULL DEFAULT '[]'::jsonb,
              covered_message_count INT NOT NULL DEFAULT 0,
              token_estimate INT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_thread_summaries_thread_created_at ON agent_thread_summaries (thread_id, created_at DESC)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_memory_profile (
              user_id UUID PRIMARY KEY REFERENCES user_profile(user_id) ON DELETE CASCADE,
              profile_version INT NOT NULL DEFAULT 1,
              display_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
              drink_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
              interaction_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
              budget_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
              health_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
              memory_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_memory_items (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              user_id UUID NOT NULL REFERENCES user_profile(user_id) ON DELETE CASCADE,
              memory_type VARCHAR(32) NOT NULL,
              scope VARCHAR(32) NOT NULL,
              content TEXT NOT NULL,
              normalized_fact JSONB,
              source_kind VARCHAR(32) NOT NULL,
              source_ref VARCHAR(255),
              confidence NUMERIC(4,3) NOT NULL DEFAULT 0.500,
              salience NUMERIC(4,3) NOT NULL DEFAULT 0.500,
              status VARCHAR(24) NOT NULL DEFAULT 'active',
              expires_at TIMESTAMPTZ,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              last_used_at TIMESTAMPTZ
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_memory_items_user_status ON user_memory_items (user_id, status, created_at DESC)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_write_jobs (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              user_id UUID NOT NULL REFERENCES user_profile(user_id) ON DELETE CASCADE,
              thread_id UUID REFERENCES agent_threads(id) ON DELETE SET NULL,
              job_type VARCHAR(32) NOT NULL,
              payload JSONB NOT NULL,
              status VARCHAR(24) NOT NULL DEFAULT 'pending',
              attempt_count INT NOT NULL DEFAULT 0,
              last_error TEXT,
              scheduled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_write_jobs_status_scheduled_at ON memory_write_jobs (status, scheduled_at ASC)"
        )
        conn.commit()

    _SCHEMA_READY = True


def resolve_thread_key(user_id: str, thread_id: str | None = None) -> str:
    """规范化线程 key。

    将外部传入的 thread_id 统一映射成 `user-<id>:session-<session>` 格式，
    方便跨端和数据库层稳定定位会话。

    参数:
        user_id: 用户标识符。
        thread_id: 外部传入的线程 ID，可为空。

    返回:
        规范化后的线程 key。
    """
    clean = (thread_id or "").strip()
    if not clean:
        clean = f"session-{uuid4().hex[:12]}"
    if clean.startswith("user-") and ":session-" in clean:
        return clean
    if clean.startswith("session-"):
        clean = clean[len("session-") :]
    return f"user-{user_id}:session-{clean}"


def _thread_defaults(user_id: str, thread_key: str, title: str | None = None) -> dict[str, Any]:
    """构造线程默认结构。

    在本地 fallback 或数据库异常回退时，提供统一的线程数据形状。
    """
    now = _iso_now()
    return {
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


def register_thread(user_id: str, thread_id: str | None = None, title: str | None = None) -> dict[str, Any]:
    """注册线程，若已存在则返回现有线程。

    行为分两种:
    1. 数据库模式: upsert `agent_threads`
    2. 本地模式: 在 `_LOCAL_THREADS` 中创建或复用线程

    参数:
        user_id: 用户标识符。
        thread_id: 外部线程 ID。
        title: 线程标题，可选。

    返回:
        线程记录字典。
    """
    _ensure_schema()
    thread_key = resolve_thread_key(user_id, thread_id)
    pool = _pool()
    if pool is None:
        existing_id = _LOCAL_THREADS_BY_KEY.get(thread_key)
        if existing_id:
            thread = _LOCAL_THREADS[existing_id]
            if title and not thread.get("title"):
                thread["title"] = title
            # 线程被再次访问时刷新更新时间，模拟数据库中的 updated_at 行为。
            thread["updated_at"] = _iso_now()
            return thread
        thread = _thread_defaults(user_id, thread_key, title=title)
        _LOCAL_THREADS[thread["id"]] = thread
        _LOCAL_THREADS_BY_KEY[thread_key] = thread["id"]
        _LOCAL_MESSAGES.setdefault(thread["id"], [])
        _LOCAL_SUMMARIES.setdefault(thread["id"], [])
        return thread

    sql = """
    INSERT INTO agent_threads (user_id, thread_key, title, status)
    VALUES (%s, %s, %s, 'active')
    ON CONFLICT (thread_key) DO UPDATE SET
      user_id = EXCLUDED.user_id,
      title = COALESCE(EXCLUDED.title, agent_threads.title),
      status = CASE WHEN agent_threads.status = 'archived' THEN 'active' ELSE agent_threads.status END,
      updated_at = NOW(),
      archived_at = NULL
    RETURNING id::text AS id, user_id::text AS user_id, thread_key, title, status, message_count, last_user_message_at, last_agent_message_at, last_summary_at, created_at, updated_at, archived_at
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id, thread_key, title))
        row = cur.fetchone()
        conn.commit()
    return row or _thread_defaults(user_id, thread_key, title=title)


def list_threads(user_id: str) -> list[dict[str, Any]]:
    """列出用户的所有线程，按更新时间倒序返回。"""
    _ensure_schema()
    pool = _pool()
    if pool is None:
        rows = [thread for thread in _LOCAL_THREADS.values() if thread["user_id"] == user_id]
        return sorted(rows, key=lambda row: row["updated_at"], reverse=True)

    sql = """
    SELECT id::text AS id, user_id::text AS user_id, thread_key, title, status, message_count,
           last_user_message_at, last_agent_message_at, last_summary_at, created_at, updated_at, archived_at
    FROM agent_threads
    WHERE user_id = %s
    ORDER BY updated_at DESC
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id,))
        return cur.fetchall()


def get_thread(user_id: str, thread_id: str) -> dict[str, Any] | None:
    """获取单个线程记录。

    参数:
        user_id: 用户标识符。
        thread_id: 线程 ID。

    返回:
        线程记录；若不存在或不属于该用户则返回 None。
    """
    _ensure_schema()
    pool = _pool()
    if pool is None:
        thread = _LOCAL_THREADS.get(thread_id)
        if thread and thread["user_id"] == user_id:
            return thread
        return None

    sql = """
    SELECT id::text AS id, user_id::text AS user_id, thread_key, title, status, message_count,
           last_user_message_at, last_agent_message_at, last_summary_at, created_at, updated_at, archived_at
    FROM agent_threads
    WHERE id = %s AND user_id = %s
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (thread_id, user_id))
        return cur.fetchone()


def list_thread_messages(user_id: str, thread_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """列出线程消息，按创建时间正序返回。

    参数:
        user_id: 用户标识符。
        thread_id: 线程 ID。
        limit: 可选的返回条数上限。

    返回:
        消息列表。
    """
    _ensure_schema()
    pool = _pool()
    if pool is None:
        rows = [message for message in _LOCAL_MESSAGES.get(thread_id, []) if message["user_id"] == user_id]
        rows = sorted(rows, key=lambda row: row["created_at"])
        if limit is not None:
            return rows[-limit:]
        return rows

    sql = """
    SELECT id::text AS id, thread_id::text AS thread_id, user_id::text AS user_id, role, content, content_type,
           request_id, tool_name, tool_call_id, source, created_at
    FROM agent_thread_messages
    WHERE thread_id = %s AND user_id = %s
    ORDER BY created_at ASC
    """
    params: tuple[Any, ...] = (thread_id, user_id)
    if limit is not None:
        sql += " LIMIT %s"
        params = (thread_id, user_id, limit)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def persist_message(
    *,
    user_id: str,
    thread_id: str,
    role: str,
    content: str,
    content_type: str = "text",
    request_id: str | None = None,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    source: str = "agent",
) -> dict[str, Any]:
    """持久化单条线程消息，并同步线程统计字段。

    对 user/assistant 消息会累计 `message_count`，
    同时刷新 `last_user_message_at` 或 `last_agent_message_at`。

    参数:
        user_id: 用户标识符。
        thread_id: 线程 ID。
        role: 消息角色，如 user / assistant / tool。
        content: 消息内容。
        content_type: 内容类型，默认 text。
        request_id: 请求 ID，可选。
        tool_name: 工具名，可选。
        tool_call_id: 工具调用 ID，可选。
        source: 消息来源，默认 agent。

    返回:
        新写入的消息记录。
    """
    _ensure_schema()
    thread = register_thread(user_id, thread_id)
    now = _iso_now()
    pool = _pool()
    if pool is None:
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
        _LOCAL_MESSAGES.setdefault(thread["id"], []).append(row)
        # 只统计自然对话消息，不把 tool 等内部消息计入 message_count。
        if role in {"user", "assistant"}:
            thread["message_count"] = int(thread.get("message_count") or 0) + 1
        thread["updated_at"] = now
        if role == "user":
            thread["last_user_message_at"] = now
        elif role == "assistant":
            thread["last_agent_message_at"] = now
        return row

    sql = """
    INSERT INTO agent_thread_messages (
      thread_id, user_id, role, content, content_type, request_id, tool_name, tool_call_id, source
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id::text AS id, thread_id::text AS thread_id, user_id::text AS user_id, role, content, content_type,
              request_id, tool_name, tool_call_id, source, created_at
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (thread["id"], user_id, role, content, content_type, request_id, tool_name, tool_call_id, source),
        )
        row = cur.fetchone()
        cur.execute(
            """
            UPDATE agent_threads
            SET message_count = message_count + CASE WHEN %s IN ('user', 'assistant') THEN 1 ELSE 0 END,
                last_user_message_at = CASE WHEN %s = 'user' THEN NOW() ELSE last_user_message_at END,
                last_agent_message_at = CASE WHEN %s = 'assistant' THEN NOW() ELSE last_agent_message_at END,
                updated_at = NOW()
            WHERE id = %s AND user_id = %s
            """,
            (role, role, role, thread["id"], user_id),
        )
        conn.commit()
    return row or {}


def archive_thread(user_id: str, thread_id: str) -> dict[str, Any] | None:
    """归档线程。

    归档后线程不会被删除，但状态会变为 archived，保留完整历史记录。
    """
    _ensure_schema()
    pool = _pool()
    if pool is None:
        thread = _LOCAL_THREADS.get(thread_id)
        if not thread or thread["user_id"] != user_id:
            return None
        thread["status"] = "archived"
        thread["archived_at"] = _iso_now()
        thread["updated_at"] = _iso_now()
        return thread

    sql = """
    UPDATE agent_threads
    SET status = 'archived', archived_at = NOW(), updated_at = NOW()
    WHERE id = %s AND user_id = %s
    RETURNING id::text AS id, user_id::text AS user_id, thread_key, title, status, message_count,
              last_user_message_at, last_agent_message_at, last_summary_at, created_at, updated_at, archived_at
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (thread_id, user_id))
        row = cur.fetchone()
        conn.commit()
    return row


def clear_thread(user_id: str, thread_id: str) -> dict[str, Any] | None:
    """清空线程的消息和摘要，但保留线程本身。

    适用于“重开一局”场景，避免删除线程元数据。
    """
    _ensure_schema()
    pool = _pool()
    if pool is None:
        thread = _LOCAL_THREADS.get(thread_id)
        if not thread or thread["user_id"] != user_id:
            return None
        _LOCAL_MESSAGES[thread_id] = []
        _LOCAL_SUMMARIES[thread_id] = []
        thread["message_count"] = 0
        thread["last_user_message_at"] = None
        thread["last_agent_message_at"] = None
        thread["last_summary_at"] = None
        thread["updated_at"] = _iso_now()
        return thread

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM agent_thread_messages WHERE thread_id = %s AND user_id = %s", (thread_id, user_id))
        cur.execute("DELETE FROM agent_thread_summaries WHERE thread_id = %s AND user_id = %s", (thread_id, user_id))
        cur.execute(
            """
            UPDATE agent_threads
            SET message_count = 0,
                last_user_message_at = NULL,
                last_agent_message_at = NULL,
                last_summary_at = NULL,
                updated_at = NOW()
            WHERE id = %s AND user_id = %s
            RETURNING id::text AS id, user_id::text AS user_id, thread_key, title, status, message_count,
                      last_user_message_at, last_agent_message_at, last_summary_at, created_at, updated_at, archived_at
            """,
            (thread_id, user_id),
        )
        row = cur.fetchone()
        conn.commit()
    return row


def persist_summary(
    *,
    user_id: str,
    thread_id: str,
    summary_type: str,
    summary_text: str,
    open_slots: list[str] | None = None,
    covered_message_count: int = 0,
    token_estimate: int | None = None,
) -> dict[str, Any]:
    """持久化线程摘要，并更新线程的最后摘要时间。

    参数:
        user_id: 用户标识符。
        thread_id: 线程 ID。
        summary_type: 摘要类型，如 rolling。
        summary_text: 摘要正文。
        open_slots: 尚未解决的问题或待确认槽位。
        covered_message_count: 本摘要覆盖的消息条数。
        token_estimate: 粗略 token 估算值。

    返回:
        摘要记录。
    """
    _ensure_schema()
    thread = register_thread(user_id, thread_id)
    now = _iso_now()
    open_slots = list(open_slots or [])
    pool = _pool()
    if pool is None:
        row = {
            "id": _new_id("summary"),
            "thread_id": thread["id"],
            "user_id": user_id,
            "summary_type": summary_type,
            "summary_text": summary_text,
            "open_slots": open_slots,
            "covered_message_count": covered_message_count,
            "token_estimate": token_estimate,
            "created_at": now,
        }
        _LOCAL_SUMMARIES.setdefault(thread["id"], []).append(row)
        thread["last_summary_at"] = now
        thread["updated_at"] = now
        return row

    sql = """
    INSERT INTO agent_thread_summaries (
      thread_id, user_id, summary_type, summary_text, open_slots, covered_message_count, token_estimate
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    RETURNING id::text AS id, thread_id::text AS thread_id, user_id::text AS user_id, summary_type, summary_text,
              open_slots, covered_message_count, token_estimate, created_at
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (thread["id"], user_id, summary_type, summary_text, open_slots, covered_message_count, token_estimate),
        )
        row = cur.fetchone()
        cur.execute(
            "UPDATE agent_threads SET last_summary_at = NOW(), updated_at = NOW() WHERE id = %s AND user_id = %s",
            (thread["id"], user_id),
        )
        conn.commit()
    return row or {}


def list_thread_summaries(user_id: str, thread_id: str) -> list[dict[str, Any]]:
    """列出线程摘要，按创建时间倒序返回。"""
    _ensure_schema()
    pool = _pool()
    if pool is None:
        rows = [summary for summary in _LOCAL_SUMMARIES.get(thread_id, []) if summary["user_id"] == user_id]
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)

    sql = """
    SELECT id::text AS id, thread_id::text AS thread_id, user_id::text AS user_id, summary_type, summary_text,
           open_slots, covered_message_count, token_estimate, created_at
    FROM agent_thread_summaries
    WHERE thread_id = %s AND user_id = %s
    ORDER BY created_at DESC
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (thread_id, user_id))
        return cur.fetchall()


def get_latest_summary(user_id: str, thread_id: str) -> dict[str, Any] | None:
    """获取线程最新一条摘要。"""
    summaries = list_thread_summaries(user_id, thread_id)
    return summaries[0] if summaries else None


def _extract_text(value: Any) -> str:
    """从多种 message/content 结构中提取纯文本。

    兼容:
    - 直接字符串
    - 带 `content` 字段的对象
    - OpenAI 风格的 content block 列表
    - LangChain/LangGraph 常见消息对象
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    content = getattr(value, "content", value)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in {"text", "output_text"}:
                    parts.append(str(part.get("text", "")))
            else:
                text = getattr(part, "text", "")
                if text:
                    parts.append(str(text))
        return "".join(parts)
    return str(content)


def build_thread_summary(user_id: str, thread_id: str) -> dict[str, Any]:
    """基于最近若干条消息构建滚动摘要。

    这是一个轻量摘要器，不依赖 LLM，主要用于:
    - 提取最近用户表达
    - 提取最近助手回复
    - 尝试识别仍待回答的问题

    返回:
        可直接传给 `persist_summary` 的摘要结构。
    """
    messages = list_thread_messages(user_id, thread_id, limit=max(getattr(get_settings(), "memory_recent_message_window", DEFAULT_RECENT_WINDOW), DEFAULT_RECENT_WINDOW))
    user_bits = [msg["content"].strip() for msg in messages if msg.get("role") == "user" and msg.get("content")]
    assistant_bits = [msg["content"].strip() for msg in messages if msg.get("role") == "assistant" and msg.get("content")]
    open_slots: list[str] = []
    if user_bits:
        last_user = user_bits[-1]
        # 用非常轻量的启发式判断“最后一句像不像仍未回答的问题”。
        if any(mark in last_user for mark in ("?", "吗", "么", "是否", "要不要")):
            open_slots.append(last_user[:80])
    if len(user_bits) > 1 and not open_slots:
        open_slots.append(user_bits[-1][:80])

    summary_parts = []
    if user_bits:
        summary_parts.append(f"最近用户提到: {'; '.join(user_bits[-2:])}")
    if assistant_bits:
        summary_parts.append(f"最近助手回复: {'; '.join(assistant_bits[-2:])}")
    if not summary_parts:
        summary_parts.append("当前会话暂无足够上下文。")

    summary_text = "；".join(summary_parts)
    # 粗略按中文 4 字符约 1 token 估算，足够用于预算和阈值判断。
    token_estimate = max(1, len(summary_text) // 4)
    return {
        "summary_type": "rolling",
        "summary_text": summary_text,
        "open_slots": open_slots,
        "covered_message_count": len(messages),
        "token_estimate": token_estimate,
    }


def should_refresh_thread_summary(user_id: str, thread_id: str) -> bool:
    """判断线程是否需要刷新摘要。

    刷新条件:
    1. 线程存在
    2. message_count 达到阈值
    3. 最新摘要覆盖的消息数落后于当前消息数
    """
    thread = get_thread(user_id, thread_id)
    if not thread:
        return False
    trigger_count = int(getattr(get_settings(), "memory_summary_trigger_count", DEFAULT_SUMMARY_TRIGGER_COUNT) or DEFAULT_SUMMARY_TRIGGER_COUNT)
    message_count = int(thread.get("message_count") or 0)
    if message_count < trigger_count:
        return False
    latest = get_latest_summary(user_id, thread_id)
    covered = int((latest or {}).get("covered_message_count") or 0)
    return message_count > covered


def queue_memory_job(
    *,
    user_id: str,
    job_type: str,
    payload: dict[str, Any],
    thread_id: str | None = None,
    scheduled_at: str | None = None,
) -> dict[str, Any]:
    """入队一个异步记忆写入任务。

    该任务通常由对话结束后的后台流程消费，用于摘要刷新、画像更新、
    记忆抽取等非阻塞工作。
    """
    _ensure_schema()
    pool = _pool()
    now = scheduled_at or _iso_now()
    if pool is None:
        row = {
            "id": _new_id("job"),
            "user_id": user_id,
            "thread_id": thread_id,
            "job_type": job_type,
            "payload": payload,
            "status": "pending",
            "attempt_count": 0,
            "last_error": None,
            "scheduled_at": now,
            "created_at": now,
            "updated_at": now,
        }
        _LOCAL_JOBS.append(row)
        return row

    sql = """
    INSERT INTO memory_write_jobs (user_id, thread_id, job_type, payload, status, scheduled_at)
    VALUES (%s, %s, %s, %s, 'pending', COALESCE(%s::timestamptz, NOW()))
    RETURNING id::text AS id, user_id::text AS user_id, thread_id::text AS thread_id, job_type, payload, status,
              attempt_count, last_error, scheduled_at, created_at, updated_at
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id, thread_id, job_type, payload, scheduled_at))
        row = cur.fetchone()
        conn.commit()
    return row or {}


def list_memory_jobs(user_id: str | None = None) -> list[dict[str, Any]]:
    """列出 memory write jobs，可按用户过滤。"""
    _ensure_schema()
    pool = _pool()
    if pool is None:
        rows = [job for job in _LOCAL_JOBS if user_id is None or job["user_id"] == user_id]
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)

    if user_id:
        sql = """
        SELECT id::text AS id, user_id::text AS user_id, thread_id::text AS thread_id, job_type, payload, status,
               attempt_count, last_error, scheduled_at, created_at, updated_at
        FROM memory_write_jobs
        WHERE user_id = %s
        ORDER BY created_at DESC
        """
        params: tuple[Any, ...] = (user_id,)
    else:
        sql = """
        SELECT id::text AS id, user_id::text AS user_id, thread_id::text AS thread_id, job_type, payload, status,
               attempt_count, last_error, scheduled_at, created_at, updated_at
        FROM memory_write_jobs
        ORDER BY created_at DESC
        """
        params = ()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_profile(user_id: str) -> dict[str, Any]:
    """获取 Agent memory profile。

    如果数据库中不存在该用户画像，会先写入一条默认记录。
    """
    _ensure_schema()
    default_profile = {
        "profile_version": 1,
        "display_preferences": {},
        "drink_preferences": {},
        "interaction_preferences": {},
        "budget_preferences": {},
        "health_preferences": {},
        "memory_updated_at": _iso_now(),
        "created_at": _iso_now(),
        "updated_at": _iso_now(),
    }
    pool = _pool()
    if pool is None:
        profile = _LOCAL_PROFILES.setdefault(user_id, {"user_id": user_id, **default_profile})
        return profile

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_memory_profile (user_id)
            VALUES (%s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id,),
        )
        cur.execute(
            """
            SELECT user_id::text AS user_id, profile_version, display_preferences, drink_preferences,
                   interaction_preferences, budget_preferences, health_preferences,
                   memory_updated_at, created_at, updated_at
            FROM user_memory_profile
            WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()
        conn.commit()
    return row or {"user_id": user_id, **default_profile}


def merge_profile_patch(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """合并画像补丁。

    这里只做浅层 section merge，不做 `app.memory.profile` 那样的深度递归。
    因为 Agent 侧 profile 结构比较固定，按 section 更新已足够覆盖当前需求。
    """
    merged = dict(existing)
    for key in ("display_preferences", "drink_preferences", "interaction_preferences", "budget_preferences", "health_preferences"):
        if key in patch and patch[key] is not None:
            current = dict(merged.get(key) or {})
            incoming = dict(patch[key] or {})
            current.update(incoming)
            merged[key] = current
    if "profile_version" in patch and patch["profile_version"] is not None:
        merged["profile_version"] = int(patch["profile_version"])
    return merged


def patch_profile(user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """应用画像补丁并持久化。"""
    current = get_profile(user_id)
    merged = merge_profile_patch(current, patch)
    merged["memory_updated_at"] = _iso_now()
    merged["updated_at"] = _iso_now()
    pool = _pool()
    if pool is None:
        _LOCAL_PROFILES[user_id] = {"user_id": user_id, **merged}
        return _LOCAL_PROFILES[user_id]

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_memory_profile (
              user_id, profile_version, display_preferences, drink_preferences, interaction_preferences,
              budget_preferences, health_preferences, memory_updated_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (user_id) DO UPDATE SET
              profile_version = EXCLUDED.profile_version,
              display_preferences = EXCLUDED.display_preferences,
              drink_preferences = EXCLUDED.drink_preferences,
              interaction_preferences = EXCLUDED.interaction_preferences,
              budget_preferences = EXCLUDED.budget_preferences,
              health_preferences = EXCLUDED.health_preferences,
              memory_updated_at = NOW(),
              updated_at = NOW()
            RETURNING user_id::text AS user_id, profile_version, display_preferences, drink_preferences,
                      interaction_preferences, budget_preferences, health_preferences, memory_updated_at, created_at, updated_at
            """,
            (
                user_id,
                merged.get("profile_version", 1),
                merged.get("display_preferences") or {},
                merged.get("drink_preferences") or {},
                merged.get("interaction_preferences") or {},
                merged.get("budget_preferences") or {},
                merged.get("health_preferences") or {},
            ),
        )
        row = cur.fetchone()
        conn.commit()
    return row or {"user_id": user_id, **merged}


def reset_profile(user_id: str) -> dict[str, Any]:
    """重置用户画像为默认空结构。"""
    pool = _pool()
    if pool is None:
        _LOCAL_PROFILES[user_id] = {
            "user_id": user_id,
            "profile_version": 1,
            "display_preferences": {},
            "drink_preferences": {},
            "interaction_preferences": {},
            "budget_preferences": {},
            "health_preferences": {},
            "memory_updated_at": _iso_now(),
            "created_at": _iso_now(),
            "updated_at": _iso_now(),
        }
        return _LOCAL_PROFILES[user_id]

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM user_memory_profile WHERE user_id = %s", (user_id,))
        conn.commit()
    return get_profile(user_id)


def _stringify_profile_value(value: Any) -> str:
    """将画像值安全转成可展示文本。"""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool, Decimal)):
        return str(value)
    return str(value)


def format_profile_summary(profile: dict[str, Any]) -> str:
    """把结构化画像渲染成适合注入 prompt 的摘要文本。"""
    drink = profile.get("drink_preferences") or {}
    budget = profile.get("budget_preferences") or {}
    interaction = profile.get("interaction_preferences") or {}
    parts = []
    if drink:
        drink_bits = []
        if drink.get("default_sugar"):
            drink_bits.append(f"默认糖度: {_stringify_profile_value(drink.get('default_sugar'))}")
        if drink.get("default_ice"):
            drink_bits.append(f"默认冰量: {_stringify_profile_value(drink.get('default_ice'))}")
        if drink.get("preferred_brands"):
            drink_bits.append(f"偏好品牌: {', '.join(map(_stringify_profile_value, drink.get('preferred_brands') or []))}")
        if drink.get("preferred_categories"):
            drink_bits.append(f"偏好类别: {', '.join(map(_stringify_profile_value, drink.get('preferred_categories') or []))}")
        if drink_bits:
            parts.append("饮品偏好: " + "；".join(drink_bits))
    if budget:
        budget_bits = []
        if budget.get("soft_price_ceiling") is not None:
            budget_bits.append(f"预算上限: {_stringify_profile_value(budget.get('soft_price_ceiling'))}")
        if budget.get("price_sensitive") is not None:
            budget_bits.append(f"价格敏感: {_stringify_profile_value(budget.get('price_sensitive'))}")
        if budget_bits:
            parts.append("预算偏好: " + "；".join(budget_bits))
    if interaction:
        reply_style = interaction.get("reply_style")
        if reply_style:
            parts.append(f"交互偏好: 回答风格 {_stringify_profile_value(reply_style)}")
    return "\n".join(parts) if parts else "暂无稳定画像。"


def format_thread_summary(summary: dict[str, Any] | None, thread: dict[str, Any] | None = None) -> str:
    """把线程摘要渲染成 prompt 文本。"""
    if not summary:
        return "暂无可用会话摘要。"
    blocks = [f"摘要: {summary.get('summary_text') or ''}".strip()]
    if summary.get("open_slots"):
        slots = summary.get("open_slots") or []
        if slots:
            blocks.append("未完成事项: " + "；".join(map(str, slots)))
    if thread:
        blocks.append(f"会话状态: {thread.get('status') or 'active'}")
    return "\n".join(blocks)


def format_memory_lines(memories: list[dict[str, Any]]) -> str:
    """把长期记忆列表渲染成逐行文本。"""
    if not memories:
        return "暂无相关长期记忆。"
    lines = []
    for memory in memories:
        scope = memory.get("scope")
        memory_type = memory.get("memory_type")
        content = memory.get("content")
        lines.append(f"- [{scope}/{memory_type}] {content}")
    return "\n".join(lines)


def _latest_user_text(messages: Iterable[Any]) -> str:
    """从消息序列中提取最后一条用户文本。

    用于作为长期记忆检索 query 的第一候选。
    """
    for message in reversed(list(messages)):
        role = getattr(message, "type", None) or getattr(message, "role", None)
        if isinstance(message, tuple) and message:
            role = message[0]
            content = message[1] if len(message) > 1 else ""
        else:
            content = getattr(message, "content", message)
        if role == "user":
            return _extract_text(content).strip()
    return ""


def search_relevant_memories(
    user_id: str,
    query: str,
    *,
    scope: str | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """检索与当前 query 相关的长期记忆。

    当前实现分两种模式:
    1. 本地 fallback: 简单 token 包含匹配
    2. 数据库模式: 用 ILIKE 在 content / normalized_fact 上做轻量检索

    注意:
        这里还不是向量化 memory retrieval，而是偏工程化的轻量检索实现。
    """
    _ensure_schema()
    top_k = top_k or int(getattr(get_settings(), "memory_semantic_top_k", DEFAULT_SEMANTIC_TOP_K) or DEFAULT_SEMANTIC_TOP_K)
    pool = _pool()
    if pool is None:
        tokens = [part for part in query.lower().split() if part]
        rows = [
            memory
            for memory in _LOCAL_MEMORIES.values()
            if memory.get("user_id") == user_id and memory.get("status") == "active"
        ]
        if scope:
            rows = [memory for memory in rows if memory.get("scope") == scope]
        if tokens:
            rows = [
                memory
                for memory in rows
                if any(token in str(memory.get("content", "")).lower() for token in tokens)
                or any(token in str(memory.get("normalized_fact", "")).lower() for token in tokens)
            ]
        rows = sorted(rows, key=lambda row: (row.get("salience", 0.5), row.get("last_used_at") or "", row.get("created_at") or ""), reverse=True)
        selected = rows[:top_k]
        for memory in selected:
            # 读取命中的记忆时回写 last_used_at，便于后续按“最近使用”参与排序。
            memory["last_used_at"] = _iso_now()
        return selected

    query_terms = [part for part in query.split() if part]
    where = ["user_id = %s", "status = 'active'"]
    params: list[Any] = [user_id]
    if scope:
        where.append("scope = %s")
        params.append(scope)
    if query_terms:
        where.append("(content ILIKE %s OR COALESCE(normalized_fact::text, '') ILIKE %s)")
        needle = f"%{query_terms[0]}%"
        params.extend([needle, needle])
    sql = f"""
    SELECT id::text AS id, user_id::text AS user_id, memory_type, scope, content, normalized_fact, source_kind,
           source_ref, confidence, salience, status, expires_at, created_at, updated_at, last_used_at
    FROM user_memory_items
    WHERE {' AND '.join(where)}
    ORDER BY salience DESC, COALESCE(last_used_at, created_at) DESC
    LIMIT %s
    """
    params.append(top_k)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        if rows:
            ids = [row["id"] for row in rows]
            cur.execute(
                "UPDATE user_memory_items SET last_used_at = NOW(), updated_at = NOW() WHERE id = ANY(%s)",
                (ids,),
            )
            conn.commit()
        return rows


def list_memories(
    user_id: str,
    *,
    scope: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """列出用户的长期记忆，可按 scope / status 过滤。"""
    _ensure_schema()
    pool = _pool()
    if pool is None:
        rows = [memory for memory in _LOCAL_MEMORIES.values() if memory.get("user_id") == user_id]
        if scope:
            rows = [memory for memory in rows if memory.get("scope") == scope]
        if status:
            rows = [memory for memory in rows if memory.get("status") == status]
        return sorted(rows, key=lambda row: row.get("updated_at") or row.get("created_at") or "", reverse=True)

    where = ["user_id = %s"]
    params: list[Any] = [user_id]
    if scope:
        where.append("scope = %s")
        params.append(scope)
    if status:
        where.append("status = %s")
        params.append(status)
    sql = f"""
    SELECT id::text AS id, user_id::text AS user_id, memory_type, scope, content, normalized_fact, source_kind,
           source_ref, confidence, salience, status, expires_at, created_at, updated_at, last_used_at
    FROM user_memory_items
    WHERE {' AND '.join(where)}
    ORDER BY created_at DESC
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return cur.fetchall()


def upsert_memory_item(
    *,
    user_id: str,
    memory_type: str,
    scope: str,
    content: str,
    source_kind: str,
    normalized_fact: dict[str, Any] | None = None,
    source_ref: str | None = None,
    confidence: float = 0.5,
    salience: float = 0.5,
    expires_at: str | None = None,
) -> dict[str, Any]:
    """写入一条长期记忆。

    当前实现是 append-only 风格，虽然函数名叫 upsert，
    但这里并不会按业务键去重更新，而是新建一条 memory item。
    """
    _ensure_schema()
    pool = _pool()
    payload = {
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
        "status": "active",
        "expires_at": expires_at,
        "created_at": _iso_now(),
        "updated_at": _iso_now(),
        "last_used_at": None,
    }
    if pool is None:
        _LOCAL_MEMORIES[payload["id"]] = payload
        return payload

    sql = """
    INSERT INTO user_memory_items (
      user_id, memory_type, scope, content, normalized_fact, source_kind, source_ref,
      confidence, salience, expires_at
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id::text AS id, user_id::text AS user_id, memory_type, scope, content, normalized_fact, source_kind,
              source_ref, confidence, salience, status, expires_at, created_at, updated_at, last_used_at
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (user_id, memory_type, scope, content, normalized_fact, source_kind, source_ref, confidence, salience, expires_at),
        )
        row = cur.fetchone()
        conn.commit()
    return row or payload


def disable_memory_item(user_id: str, memory_id: str) -> dict[str, Any] | None:
    """禁用一条长期记忆，但不物理删除。"""
    _ensure_schema()
    pool = _pool()
    if pool is None:
        memory = _LOCAL_MEMORIES.get(memory_id)
        if not memory or memory.get("user_id") != user_id:
            return None
        memory["status"] = "disabled"
        memory["updated_at"] = _iso_now()
        return memory

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE user_memory_items
            SET status = 'disabled', updated_at = NOW()
            WHERE id = %s AND user_id = %s
            RETURNING id::text AS id, user_id::text AS user_id, memory_type, scope, content, normalized_fact,
                      source_kind, source_ref, confidence, salience, status, expires_at, created_at, updated_at, last_used_at
            """,
            (memory_id, user_id),
        )
        row = cur.fetchone()
        conn.commit()
    return row


def delete_memory_item(user_id: str, memory_id: str) -> dict[str, Any] | None:
    """物理删除一条长期记忆。"""
    _ensure_schema()
    pool = _pool()
    if pool is None:
        memory = _LOCAL_MEMORIES.get(memory_id)
        if not memory or memory.get("user_id") != user_id:
            return None
        return _LOCAL_MEMORIES.pop(memory_id)

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM user_memory_items WHERE id = %s AND user_id = %s RETURNING id::text AS id", (memory_id, user_id))
        row = cur.fetchone()
        conn.commit()
    return row


def refresh_profile_from_records(user_id: str) -> dict[str, Any]:
    """基于记录为画像补默认值。

    当前实现比较保守，只在缺省时补入默认糖度和冰度，
    更复杂的偏好推导交给其他 memory/profile 模块处理。
    """
    profile = get_profile(user_id)
    drink_preferences = dict(profile.get("drink_preferences") or {})
    if not drink_preferences.get("default_sugar"):
        drink_preferences["default_sugar"] = "少糖"
    if not drink_preferences.get("default_ice"):
        drink_preferences["default_ice"] = "少冰"
    return patch_profile(user_id, {"drink_preferences": drink_preferences})


def load_prompt_context(
    user_id: str,
    thread_id: str,
    *,
    messages: Iterable[Any] | None = None,
    system_prompt: str,
    recent_window: int | None = None,
    semantic_top_k: int | None = None,
) -> list[Any]:
    """装配注入给 Agent 的 prompt 上下文。

    拼装顺序是:
    1. 原始 system prompt
    2. 用户画像摘要
    3. 当前会话摘要
    4. 相关长期记忆
    5. 最近窗口内的非 system 历史消息

    其中长期记忆检索的 query 优先取“最近一条用户消息”，
    如果拿不到，再退化到线程摘要文本。
    """
    thread = get_thread(user_id, thread_id)
    profile = get_profile(user_id)
    latest_summary = get_latest_summary(user_id, thread_id)
    recent_window = recent_window or int(getattr(get_settings(), "memory_recent_message_window", DEFAULT_RECENT_WINDOW) or DEFAULT_RECENT_WINDOW)
    semantic_top_k = semantic_top_k or int(getattr(get_settings(), "memory_semantic_top_k", DEFAULT_SEMANTIC_TOP_K) or DEFAULT_SEMANTIC_TOP_K)
    recent_messages = list(messages or [])[-recent_window:]
    query = _latest_user_text(recent_messages) or (latest_summary or {}).get("summary_text") or ""
    memories = search_relevant_memories(user_id, query=query, top_k=semantic_top_k)

    # 先把 memory 相关上下文压成多个 system block，
    # 这样对主模型来说边界更清晰，也方便后续独立调整每个 block 的格式。
    blocks = [system_prompt.strip()]
    blocks.append(f"用户画像:\n{format_profile_summary(profile)}")
    blocks.append(f"当前会话:\n{format_thread_summary(latest_summary, thread)}")
    blocks.append(f"相关长期记忆:\n{format_memory_lines(memories)}")

    prompt_messages: list[Any] = [("system", block) for block in blocks if block.strip()]
    for message in recent_messages:
        role = getattr(message, "type", None) or getattr(message, "role", None)
        if isinstance(message, tuple):
            role = message[0]
        # 避免把上游 system 消息重复注入，防止 system prompt 污染和指令冲突。
        if role == "system":
            continue
        prompt_messages.append(message)
    return prompt_messages
