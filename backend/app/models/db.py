from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlmodel import SQLModel

from app.core.config import get_settings


class Menu(SQLModel):
    id: str | None = None
    brand: str
    name: str
    size: str | None = None
    price: Decimal | None = None
    description: str | None = None
    is_active: bool = True


class Record(SQLModel):
    id: str | None = None
    user_id: str | None = None
    menu_id: str | None = None
    brand: str
    name: str
    sugar: str | None = None
    ice: str | None = None
    mood: str | None = None
    price: Decimal | None = None
    photo_url: str | None = None
    source: str
    notes: str | None = None
    consumed_at: datetime


class RecordPhoto(SQLModel):
    id: str | None = None
    record_id: str
    photo_url: str
    sort_order: int = 0
    created_at: datetime | None = None


class UserProfile(SQLModel):
    user_id: str | None = None
    username: str
    password_hash: str
    nickname: str | None = None


_pool: ConnectionPool | None = None
_schema_ready = False
_memory_schema_ready = False


def _ensure_records_user_schema() -> None:
    global _schema_ready
    if _schema_ready or _pool is None:
        return

    with _pool.connection() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE menu ADD COLUMN IF NOT EXISTS description TEXT")
        cur.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS user_id UUID")
        cur.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS mood VARCHAR(120)")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS record_photos (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              record_id UUID NOT NULL REFERENCES records(id) ON DELETE CASCADE,
              photo_url TEXT NOT NULL,
              sort_order INT NOT NULL DEFAULT 0,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_record_photos_record_sort ON record_photos (record_id, sort_order, created_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_user_consumed_at ON records (user_id, consumed_at)"
        )
        cur.execute("ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS nickname VARCHAR(80)")
        conn.commit()

    _schema_ready = True


def _ensure_memory_schema() -> None:
    global _memory_schema_ready
    if _memory_schema_ready or _pool is None:
        return

    with _pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_threads (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              user_id UUID NOT NULL REFERENCES user_profile(user_id) ON DELETE CASCADE,
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
              user_id UUID NOT NULL REFERENCES user_profile(user_id) ON DELETE CASCADE,
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
              user_id UUID NOT NULL REFERENCES user_profile(user_id) ON DELETE CASCADE,
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

    _memory_schema_ready = True


def init_pool() -> None:
    global _pool
    settings = get_settings()
    if not settings.database_url:
        return
    if _pool is None:
        _pool = ConnectionPool(conninfo=settings.database_url, kwargs={"row_factory": dict_row}, open=True)
        _ensure_records_user_schema()
        _ensure_memory_schema()


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def has_pool() -> bool:
    return _pool is not None


def get_pool() -> ConnectionPool | None:
    return _pool


def get_pool() -> ConnectionPool | None:
    return _pool


def _normalize_item_photos(item: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    raw_photos = item.get("photos") or []

    for index, photo in enumerate(raw_photos):
        url = str((photo or {}).get("url") or "").strip()
        if not url:
            continue
        sort_order = (photo or {}).get("sort_order")
        normalized.append(
            {
                "url": url,
                "sort_order": int(sort_order if sort_order is not None else index),
            }
        )

    if not normalized:
        fallback_url = str(item.get("photo_url") or "").strip()
        if fallback_url:
            normalized.append({"url": fallback_url, "sort_order": 0})

    return normalized[:3]


def _with_photo_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    payload["photos"] = _normalize_item_photos(item)
    payload["photo_url"] = payload["photos"][0]["url"] if payload["photos"] else None
    return payload


def _attach_photos_to_records(records: list[dict[str, Any]], photo_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in photo_rows:
        record_id = str(row.get("record_id") or "").strip()
        if not record_id:
            continue
        grouped.setdefault(record_id, []).append(
            {
                "url": row["photo_url"],
                "sort_order": row.get("sort_order") or 0,
                "created_at": row.get("created_at"),
            }
        )

    for record in records:
        photos = grouped.get(str(record["id"]), [])
        photos.sort(key=lambda item: (item["sort_order"], str(item["created_at"] or "")))
        record["photos"] = photos
        record["photo_url"] = photos[0]["url"] if photos else record.get("photo_url")

    return records


def _brand_color(brand: str) -> str:
    mapping = {
        "HEYTEA": "#ADFF2F",
        "喜茶": "#ADFF2F",
        "COCO": "#FFB7C5",
        "奈雪": "#FFA5B4",
        "NAYUKI": "#FFA5B4",
    }
    return mapping.get(brand, "#E5E7EB")


def authenticate_user(username: str) -> dict[str, Any] | None:
    if not _pool:
        return None
    with _pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, username, password_hash, nickname FROM user_profile WHERE username = %s",
            (username,),
        )
        return cur.fetchone()


def create_user(username: str, password_hash: str, nickname: str | None = None) -> dict[str, Any]:
    normalized_username = username.strip().lower()
    if not normalized_username:
        raise ValueError("username is required")

    existing = authenticate_user(normalized_username)
    if existing:
        raise ValueError("username already exists")

    if not _pool:
        return {
            "user_id": f"local-{normalized_username}",
            "username": normalized_username,
            "password_hash": password_hash,
            "nickname": nickname,
        }

    sql = """
    INSERT INTO user_profile (username, password_hash, nickname)
    VALUES (%s, %s, %s)
    RETURNING user_id::text AS user_id, username, password_hash, nickname
    """
    with _pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (normalized_username, password_hash, nickname))
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise RuntimeError("failed to create user")
    return row


def insert_records(user_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not _pool:
        now = datetime.utcnow()
        result: list[dict[str, Any]] = []
        for idx, item in enumerate(items, start=1):
            payload = _with_photo_payload(item)
            result.append(
                {
                    "id": f"local-{idx}",
                    "user_id": user_id,
                    "brand": payload["brand"],
                    "name": payload["name"],
                    "size": payload.get("size"),
                    "sugar": payload.get("sugar"),
                    "ice": payload.get("ice"),
                    "mood": payload.get("mood"),
                    "price": payload.get("price") or Decimal("0"),
                    "photo_url": payload.get("photo_url"),
                    "photos": [
                        {
                            "url": photo["url"],
                            "sort_order": photo["sort_order"],
                            "created_at": now,
                        }
                        for photo in payload["photos"]
                    ],
                    "source": payload["source"],
                    "notes": payload.get("notes"),
                    "consumed_at": payload["consumed_at"],
                    "created_at": now,
                }
            )
        return result

    insert_record_sql = """
    INSERT INTO records (user_id, menu_id, brand, name, sugar, ice, mood, price, photo_url, source, notes, consumed_at)
    VALUES (%(user_id)s, %(menu_id)s, %(brand)s, %(name)s, %(sugar)s, %(ice)s, %(mood)s, %(price)s, %(photo_url)s, %(source)s, %(notes)s, %(consumed_at)s)
    RETURNING id::text, user_id::text AS user_id, brand, name, NULL::varchar AS size, sugar, ice, mood, price, photo_url, source, notes, consumed_at, created_at
    """
    insert_photo_sql = """
    INSERT INTO record_photos (record_id, photo_url, sort_order)
    VALUES (%(record_id)s::uuid, %(photo_url)s, %(sort_order)s)
    RETURNING record_id::text AS record_id, photo_url, sort_order, created_at
    """
    inserted: list[dict[str, Any]] = []
    inserted_photos: list[dict[str, Any]] = []
    with _pool.connection() as conn, conn.cursor() as cur:
        for item in items:
            payload = _with_photo_payload(item)
            payload["user_id"] = user_id
            cur.execute(insert_record_sql, payload)
            row = cur.fetchone()
            if row:
                inserted.append(row)
                for photo in payload["photos"]:
                    cur.execute(
                        insert_photo_sql,
                        {
                            "record_id": row["id"],
                            "photo_url": photo["url"],
                            "sort_order": photo["sort_order"],
                        },
                    )
                    photo_row = cur.fetchone()
                    if photo_row:
                        inserted_photos.append(photo_row)
        conn.commit()
    return _attach_photos_to_records(inserted, inserted_photos)


def query_calendar(user_id: str, year: int, month: int) -> dict[str, list[dict[str, str]]]:
    if not _pool:
        return {}
    first_day = date(year, month, 1)
    last_day = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1)

    sql = """
    SELECT DATE(consumed_at) AS d, brand, COUNT(*) AS c
    FROM records
    WHERE user_id = %s AND consumed_at >= %s AND consumed_at < %s
    GROUP BY DATE(consumed_at), brand
    ORDER BY d ASC, c DESC
    """
    output: dict[str, list[dict[str, str]]] = {}
    with _pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id, first_day, last_day))
        for row in cur.fetchall():
            key = row["d"].isoformat()
            arr = output.setdefault(key, [])
            if len(arr) < 3:
                arr.append({"brand": row["brand"], "color": _brand_color(row["brand"])})
    return output


def query_day(user_id: str, day: date) -> dict[str, Any]:
    if not _pool:
        return {"records": [], "photos": [], "total": Decimal("0")}

    records_sql = """
    SELECT id::text, brand, name, NULL::varchar AS size, sugar, ice, mood, price, photo_url, source, notes, consumed_at, created_at
    FROM records
    WHERE user_id = %s AND consumed_at >= %s AND consumed_at < %s
    ORDER BY consumed_at DESC
    """
    photos_sql = """
    SELECT record_id::text AS record_id, photo_url, sort_order, created_at
    FROM record_photos
    WHERE record_id::text = ANY(%s)
    ORDER BY sort_order ASC, created_at ASC
    """
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    with _pool.connection() as conn, conn.cursor() as cur:
        cur.execute(records_sql, (user_id, start, end))
        records = cur.fetchall()
        photo_rows: list[dict[str, Any]] = []
        if records:
            cur.execute(photos_sql, ([str(record["id"]) for record in records],))
            photo_rows = cur.fetchall()

    records = _attach_photos_to_records(records, photo_rows)
    photos = [photo["url"] for record in records for photo in record.get("photos", [])]
    if not photos:
        photos = [r["photo_url"] for r in records if r.get("photo_url")]
    total = sum((r.get("price") or Decimal("0")) for r in records)
    return {"records": records, "photos": photos, "total": total}


def query_recent(user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    if not _pool:
        return []

    records_sql = """
    SELECT id::text, brand, name, NULL::varchar AS size, sugar, ice, mood, price, photo_url, source, notes, consumed_at, created_at
    FROM records
    WHERE user_id = %s
    ORDER BY consumed_at DESC, created_at DESC
    LIMIT %s
    """
    photos_sql = """
    SELECT record_id::text AS record_id, photo_url, sort_order, created_at
    FROM record_photos
    WHERE record_id::text = ANY(%s)
    ORDER BY sort_order ASC, created_at ASC
    """

    with _pool.connection() as conn, conn.cursor() as cur:
        cur.execute(records_sql, (user_id, limit))
        records = cur.fetchall()
        photo_rows: list[dict[str, Any]] = []
        if records:
            cur.execute(photos_sql, ([str(record["id"]) for record in records],))
            photo_rows = cur.fetchall()

    return _attach_photos_to_records(records, photo_rows)


def delete_record(user_id: str, record_id: str) -> bool:
    if not _pool:
        return False

    sql = """
    DELETE FROM records
    WHERE id = %s::uuid AND user_id = %s
    RETURNING id::text AS id
    """

    with _pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (record_id, user_id))
        deleted = cur.fetchone()
        conn.commit()

    return deleted is not None


def query_stats(user_id: str, period: str, date_str: str | None) -> dict[str, Any]:
    if not _pool:
        return {
            "total_amount": Decimal("0"),
            "total_count": 0,
            "brand_dist": [],
            "weekly_trend": [],
            "sugar_pref": [],
            "ice_pref": [],
            "daily_density": {},
        }

    where = "user_id = %s"
    params: list[Any] = [user_id]
    if period == "month" and date_str:
        year, month = map(int, date_str[:7].split("-"))
        start = date(year, month, 1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        where = "user_id = %s AND consumed_at >= %s AND consumed_at < %s"
        params = [user_id, start, end]
    elif period == "week" and date_str:
        anchor_source = date_str[:10] if len(date_str) >= 10 else f"{date_str}-01"
        anchor = date.fromisoformat(anchor_source)
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=7)
        where = "user_id = %s AND consumed_at >= %s AND consumed_at < %s"
        params = [user_id, start, end]

    with _pool.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COALESCE(SUM(price),0) AS total_amount, COUNT(*) AS total_count FROM records WHERE {where}", params)
        totals = cur.fetchone() or {"total_amount": Decimal("0"), "total_count": 0}

        cur.execute(
            f"SELECT brand, COUNT(*) AS count FROM records WHERE {where} GROUP BY brand ORDER BY count DESC",
            params,
        )
        brands = cur.fetchall()

        cur.execute(
            f"SELECT TO_CHAR(DATE_TRUNC('week', consumed_at), '\"W\"IW') AS week, COUNT(*) AS count FROM records WHERE {where} GROUP BY week ORDER BY week",
            params,
        )
        weekly = cur.fetchall()

        cur.execute(
            f"SELECT sugar, COUNT(*) AS count FROM records WHERE {where} AND sugar IS NOT NULL GROUP BY sugar ORDER BY count DESC LIMIT 3",
            params,
        )
        sugar = cur.fetchall()

        cur.execute(
            f"SELECT ice, COUNT(*) AS count FROM records WHERE {where} AND ice IS NOT NULL GROUP BY ice ORDER BY count DESC LIMIT 3",
            params,
        )
        ice = cur.fetchall()

        cur.execute(
            f"SELECT DATE(consumed_at) AS day, COUNT(*) AS count FROM records WHERE {where} GROUP BY day ORDER BY day",
            params,
        )
        daily = cur.fetchall()

    total_count = int(totals["total_count"] or 0)
    brand_dist = []
    for row in brands:
        count = int(row["count"])
        pct = round((count / total_count) * 100, 2) if total_count else 0.0
        brand_dist.append({"brand": row["brand"], "count": count, "pct": pct})

    return {
        "total_amount": totals["total_amount"] or Decimal("0"),
        "total_count": total_count,
        "brand_dist": brand_dist,
        "weekly_trend": [{"week": row["week"], "count": int(row["count"])} for row in weekly],
        "sugar_pref": [{"sugar": row["sugar"], "count": int(row["count"])} for row in sugar],
        "ice_pref": [{"ice": row["ice"], "count": int(row["count"])} for row in ice],
        "daily_density": {row["day"].isoformat(): int(row["count"]) for row in daily},
    }
