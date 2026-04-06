from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal

os.environ["JWT_SECRET"] = "test-secret"

from fastapi.testclient import TestClient
import pytest

from app.api import records as records_api
from app.core.security import create_access_token
from app.main import app
from app.models import db as db_module


client = TestClient(app)


def _auth_header(user_id: str = "u-test") -> dict[str, str]:
    token = create_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


class _FakeCursor:
    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.calls: list[tuple[str, tuple | None]] = []
        self._current = -1

    def execute(self, sql: str, params=None) -> None:
        self.calls.append((sql, params))
        self._current = len(self.calls) - 1

    def fetchone(self):
        response = self.responses[self._current]
        return response.get("one")

    def fetchall(self):
        response = self.responses[self._current]
        return response.get("all", [])


class _FakeCursorContext:
    def __init__(self, cursor: _FakeCursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self):
        return _FakeCursorContext(self._cursor)

    def commit(self):
        self.committed = True


class _FakeConnContext:
    def __init__(self, conn: _FakeConn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, cursor: _FakeCursor):
        self.conn = _FakeConn(cursor)

    def connection(self):
        return _FakeConnContext(self.conn)


def test_confirm_records_uses_request_user_id_not_body(monkeypatch):
    captured: dict[str, object] = {}

    def fake_insert_records(user_id: str, items: list[dict]):
        captured["user_id"] = user_id
        captured["items"] = items
        return [
            {
                "id": "r-1",
                "user_id": user_id,
                "brand": "喜茶",
                "name": "多肉葡萄",
                "size": "大杯",
                "sugar": "少糖",
                "ice": "少冰",
                "mood": "开心",
                "price": Decimal("19.00"),
                "photo_url": None,
                "source": "manual",
                "consumed_at": datetime.utcnow().isoformat() + "Z",
                "created_at": datetime.utcnow().isoformat() + "Z",
            }
        ]

    monkeypatch.setattr(records_api, "insert_records", fake_insert_records)

    payload = {
        "user_id": "fake-user",
        "items": [
            {
                "brand": "喜茶",
                "name": "多肉葡萄",
                "size": "大杯",
                "sugar": "少糖",
                "ice": "少冰",
                "mood": "开心",
                "price": 19,
                "source": "manual",
                "consumed_at": datetime.utcnow().isoformat() + "Z",
            }
        ],
    }
    resp = client.post("/bobo/records/confirm", json=payload, headers=_auth_header("real-user"))

    assert resp.status_code == 201
    assert captured["user_id"] == "real-user"
    assert captured["items"][0]["brand"] == "喜茶"
    assert resp.json()["inserted"] == 1


def test_confirm_records_returns_400_when_daily_limit_exceeded(monkeypatch):
    def fake_insert_records(user_id: str, items: list[dict]):
        raise ValueError("2026-04-06 最多只能保存 10 条饮品记录")

    monkeypatch.setattr(records_api, "insert_records", fake_insert_records)

    payload = {
        "items": [
            {
                "brand": "喜茶",
                "name": "多肉葡萄",
                "price": 19,
                "source": "manual",
                "consumed_at": "2026-04-06T12:00:00Z",
            }
        ],
    }

    resp = client.post("/bobo/records/confirm", json=payload, headers=_auth_header("real-user"))

    assert resp.status_code == 400
    assert resp.json()["detail"] == "2026-04-06 最多只能保存 10 条饮品记录"


def test_get_day_uses_request_user_id(monkeypatch):
    captured: dict[str, object] = {}

    def fake_query_day(user_id: str, day: date):
        captured["user_id"] = user_id
        captured["day"] = day
        return {"records": [], "photos": [], "total": Decimal("0")}

    monkeypatch.setattr(records_api, "query_day", fake_query_day)

    resp = client.get("/bobo/records/day?date=2026-03-28", headers=_auth_header("real-user"))

    assert resp.status_code == 200
    assert captured["user_id"] == "real-user"
    assert captured["day"] == date(2026, 3, 28)


def test_get_day_signs_photo_urls_for_display(monkeypatch):
    def fake_query_day(user_id: str, day: date):
        assert user_id == "real-user"
        assert day == date(2026, 3, 28)
        return {
            "records": [
                {
                    "id": "r-1",
                    "brand": "喜茶",
                    "name": "多肉葡萄",
                    "size": None,
                    "sugar": "少糖",
                    "ice": "少冰",
                    "mood": None,
                    "price": Decimal("19.00"),
                    "photo_url": "https://cos.example.com/raw-cover.jpg",
                    "photos": [
                        {
                            "url": "https://cos.example.com/raw-cover.jpg",
                            "sort_order": 0,
                            "created_at": datetime.utcnow(),
                        },
                        {
                            "url": "https://cos.example.com/raw-detail.jpg",
                            "sort_order": 1,
                            "created_at": datetime.utcnow(),
                        },
                    ],
                    "source": "manual",
                    "notes": None,
                    "consumed_at": datetime.utcnow().isoformat() + "Z",
                    "created_at": datetime.utcnow().isoformat() + "Z",
                }
            ],
            "photos": [
                "https://cos.example.com/raw-cover.jpg",
                "https://cos.example.com/raw-detail.jpg",
            ],
            "total": Decimal("19.00"),
        }

    monkeypatch.setattr(records_api, "query_day", fake_query_day)
    monkeypatch.setattr(records_api._cos_service, "get_display_url", lambda url: f"{url}?signed=1")

    resp = client.get("/bobo/records/day?date=2026-03-28", headers=_auth_header("real-user"))

    assert resp.status_code == 200
    data = resp.json()
    assert data["photos"] == [
        "https://cos.example.com/raw-cover.jpg?signed=1",
        "https://cos.example.com/raw-detail.jpg?signed=1",
    ]
    assert data["records"][0]["photo_url"] == "https://cos.example.com/raw-cover.jpg?signed=1"
    assert data["records"][0]["photos"][0]["url"] == "https://cos.example.com/raw-cover.jpg?signed=1"
    assert data["records"][0]["photos"][1]["url"] == "https://cos.example.com/raw-detail.jpg?signed=1"


def test_get_calendar_uses_request_user_id(monkeypatch):
    captured: dict[str, object] = {}

    def fake_query_calendar(user_id: str, year: int, month: int):
        captured["user_id"] = user_id
        captured["year"] = year
        captured["month"] = month
        return {}

    monkeypatch.setattr(records_api, "query_calendar", fake_query_calendar)

    resp = client.get("/bobo/records/calendar?year=2026&month=3", headers=_auth_header("real-user"))

    assert resp.status_code == 200
    assert captured["user_id"] == "real-user"
    assert captured["year"] == 2026
    assert captured["month"] == 3


def test_get_recent_uses_request_user_id(monkeypatch):
    captured: dict[str, object] = {}

    def fake_query_recent(user_id: str, limit: int):
        captured["user_id"] = user_id
        captured["limit"] = limit
        return []

    monkeypatch.setattr(records_api, "query_recent", fake_query_recent)

    resp = client.get("/bobo/records/recent?limit=5", headers=_auth_header("real-user"))

    assert resp.status_code == 200
    assert captured["user_id"] == "real-user"
    assert captured["limit"] == 5


def test_delete_record_uses_request_user_id(monkeypatch):
    captured: dict[str, object] = {}

    def fake_delete_record(user_id: str, record_id: str):
        captured["user_id"] = user_id
        captured["record_id"] = record_id
        return True

    monkeypatch.setattr(records_api, "delete_record", fake_delete_record)

    resp = client.delete("/bobo/records/r-123", headers=_auth_header("real-user"))

    assert resp.status_code == 204
    assert captured["user_id"] == "real-user"
    assert captured["record_id"] == "r-123"


def test_delete_record_returns_404_when_missing(monkeypatch):
    monkeypatch.setattr(records_api, "delete_record", lambda user_id, record_id: False)

    resp = client.delete("/bobo/records/r-missing", headers=_auth_header("real-user"))

    assert resp.status_code == 404
    assert resp.json()["detail"] == "record not found"


def test_get_stats_uses_request_user_id(monkeypatch):
    captured: dict[str, object] = {}

    def fake_query_stats(user_id: str, period: str, date_str: str | None):
        captured["user_id"] = user_id
        captured["period"] = period
        captured["date_str"] = date_str
        return {
            "total_amount": Decimal("0"),
            "total_count": 0,
            "brand_dist": [],
            "weekly_trend": [],
            "sugar_pref": [],
            "ice_pref": [],
            "daily_density": {},
        }

    monkeypatch.setattr(records_api, "query_stats", fake_query_stats)

    resp = client.get("/bobo/records/stats?period=month&date=2026-03", headers=_auth_header("real-user"))

    assert resp.status_code == 200
    assert captured["user_id"] == "real-user"
    assert captured["period"] == "month"
    assert captured["date_str"] == "2026-03"


def test_insert_records_includes_user_id():
    cursor = _FakeCursor(
        responses=[
            {"one": {"count": 0}},
            {
                "one": {
                    "id": "r-1",
                    "user_id": "u-1",
                    "brand": "喜茶",
                    "name": "多肉葡萄",
                    "size": None,
                    "sugar": "少糖",
                    "ice": "少冰",
                    "mood": "开心",
                    "price": Decimal("19.00"),
                    "photo_url": None,
                    "source": "manual",
                    "consumed_at": datetime.utcnow(),
                    "created_at": datetime.utcnow(),
                }
            }
        ]
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db_module, "_pool", _FakePool(cursor))
    try:
        rows = db_module.insert_records(
            "u-1",
            [
                {
                    "menu_id": None,
                    "brand": "喜茶",
                    "name": "多肉葡萄",
                    "size": "大杯",
                    "sugar": "少糖",
                    "ice": "少冰",
                    "mood": "开心",
                    "price": Decimal("19.00"),
                    "photo_url": None,
                    "source": "manual",
                    "notes": None,
                    "consumed_at": datetime.utcnow().isoformat() + "Z",
                }
            ],
        )
    finally:
        monkeypatch.undo()

    assert rows[0]["user_id"] == "u-1"
    assert rows[0]["mood"] == "开心"
    assert "SELECT COUNT(*) AS count" in cursor.calls[0][0]
    assert cursor.calls[1][1]["user_id"] == "u-1"
    assert cursor.calls[1][1]["mood"] == "开心"
    assert "INSERT INTO records" in cursor.calls[1][0]


def test_insert_records_persists_photos_and_backfills_photo_url():
    cursor = _FakeCursor(
        responses=[
            {"one": {"count": 0}},
            {
                "one": {
                    "id": "r-9",
                    "user_id": "u-9",
                    "brand": "喜茶",
                    "name": "多肉葡萄",
                    "size": None,
                    "sugar": "少糖",
                    "ice": "少冰",
                    "mood": "开心",
                    "price": Decimal("19.00"),
                    "photo_url": "https://cdn.example.com/1.jpg",
                    "source": "manual",
                    "consumed_at": datetime.utcnow(),
                    "created_at": datetime.utcnow(),
                }
            },
            {
                "one": {
                    "record_id": "r-9",
                    "photo_url": "https://cdn.example.com/1.jpg",
                    "sort_order": 0,
                    "created_at": datetime.utcnow(),
                }
            },
            {
                "one": {
                    "record_id": "r-9",
                    "photo_url": "https://cdn.example.com/2.jpg",
                    "sort_order": 1,
                    "created_at": datetime.utcnow(),
                }
            },
        ]
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db_module, "_pool", _FakePool(cursor))
    try:
        rows = db_module.insert_records(
            "u-9",
            [
                {
                    "menu_id": None,
                    "brand": "喜茶",
                    "name": "多肉葡萄",
                    "size": None,
                    "sugar": "少糖",
                    "ice": "少冰",
                    "mood": "开心",
                    "price": Decimal("19.00"),
                    "photos": [
                        {"url": "https://cdn.example.com/1.jpg", "sort_order": 0},
                        {"url": "https://cdn.example.com/2.jpg", "sort_order": 1},
                    ],
                    "source": "manual",
                    "notes": None,
                    "consumed_at": datetime.utcnow().isoformat() + "Z",
                }
            ],
        )
    finally:
        monkeypatch.undo()

    assert rows[0]["photo_url"] == "https://cdn.example.com/1.jpg"
    assert [photo["url"] for photo in rows[0]["photos"]] == [
        "https://cdn.example.com/1.jpg",
        "https://cdn.example.com/2.jpg",
    ]
    assert "INSERT INTO record_photos" in cursor.calls[2][0]
    assert cursor.calls[2][1]["record_id"] == "r-9"


def test_insert_records_rejects_when_daily_limit_would_be_exceeded():
    cursor = _FakeCursor(
        responses=[
            {"one": {"count": 9}},
        ]
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db_module, "_pool", _FakePool(cursor))
    try:
        with pytest.raises(ValueError, match="最多只能保存 10 条饮品记录"):
            db_module.insert_records(
                "u-1",
                [
                    {
                        "menu_id": None,
                        "brand": "喜茶",
                        "name": "多肉葡萄",
                        "size": None,
                        "sugar": "少糖",
                        "ice": "少冰",
                        "mood": None,
                        "price": Decimal("19.00"),
                        "source": "manual",
                        "notes": None,
                        "consumed_at": "2026-04-06T12:00:00Z",
                    },
                    {
                        "menu_id": None,
                        "brand": "霸王茶姬",
                        "name": "伯牙绝弦",
                        "size": None,
                        "sugar": "正常",
                        "ice": "少冰",
                        "mood": None,
                        "price": Decimal("18.00"),
                        "source": "manual",
                        "notes": None,
                        "consumed_at": "2026-04-06T18:00:00Z",
                    },
                ],
            )
    finally:
        monkeypatch.undo()

    assert len(cursor.calls) == 1
    assert "SELECT COUNT(*) AS count" in cursor.calls[0][0]


def test_query_day_filters_by_user_id():
    cursor = _FakeCursor(
        responses=[
            {
                "all": [
                    {
                        "id": "r-1",
                        "brand": "喜茶",
                        "name": "多肉葡萄",
                        "size": None,
                        "sugar": "少糖",
                        "ice": "少冰",
                        "mood": "满足",
                        "price": Decimal("19.00"),
                        "photo_url": "https://cdn.example.com/cover.jpg",
                        "source": "manual",
                        "consumed_at": datetime(2026, 3, 28, 12, 0, 0),
                        "created_at": datetime(2026, 3, 28, 12, 0, 0),
                    }
                ]
            },
            {
                "all": [
                    {
                        "record_id": "r-1",
                        "photo_url": "https://cdn.example.com/1.jpg",
                        "sort_order": 0,
                        "created_at": datetime(2026, 3, 28, 12, 0, 1),
                    },
                    {
                        "record_id": "r-1",
                        "photo_url": "https://cdn.example.com/2.jpg",
                        "sort_order": 1,
                        "created_at": datetime(2026, 3, 28, 12, 0, 2),
                    },
                ]
            },
        ]
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db_module, "_pool", _FakePool(cursor))
    try:
        result = db_module.query_day("u-2", date(2026, 3, 28))
    finally:
        monkeypatch.undo()

    sql, params = cursor.calls[0]
    assert "user_id = %s" in sql
    assert params[0] == "u-2"
    assert result["records"][0]["brand"] == "喜茶"
    assert result["records"][0]["mood"] == "满足"
    assert [photo["url"] for photo in result["records"][0]["photos"]] == [
        "https://cdn.example.com/1.jpg",
        "https://cdn.example.com/2.jpg",
    ]
    assert result["photos"] == [
        "https://cdn.example.com/1.jpg",
        "https://cdn.example.com/2.jpg",
    ]


def test_query_calendar_filters_by_user_id():
    cursor = _FakeCursor(
        responses=[
            {
                "all": [
                    {"d": date(2026, 3, 28), "brand": "喜茶", "c": 2},
                    {"d": date(2026, 3, 28), "brand": "奈雪", "c": 1},
                ]
            }
        ]
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db_module, "_pool", _FakePool(cursor))
    try:
        result = db_module.query_calendar("u-3", 2026, 3)
    finally:
        monkeypatch.undo()

    sql, params = cursor.calls[0]
    assert "user_id = %s" in sql
    assert params[0] == "u-3"
    assert "2026-03-28" in result
    assert result["2026-03-28"][0]["brand"] == "喜茶"


def test_query_recent_filters_by_user_id_and_limit():
    cursor = _FakeCursor(
        responses=[
            {
                "all": [
                    {
                        "id": "r-1",
                        "brand": "喜茶",
                        "name": "多肉葡萄",
                        "size": None,
                        "sugar": "少糖",
                        "ice": "少冰",
                        "mood": "满足",
                        "price": Decimal("19.00"),
                        "photo_url": "https://cdn.example.com/cover.jpg",
                        "source": "manual",
                        "notes": None,
                        "consumed_at": datetime(2026, 3, 28, 12, 0, 0),
                        "created_at": datetime(2026, 3, 28, 12, 0, 1),
                    }
                ]
            },
            {
                "all": [
                    {
                        "record_id": "r-1",
                        "photo_url": "https://cdn.example.com/1.jpg",
                        "sort_order": 0,
                        "created_at": datetime(2026, 3, 28, 12, 0, 2),
                    }
                ]
            },
        ]
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db_module, "_pool", _FakePool(cursor))
    try:
        result = db_module.query_recent("u-5", 5)
    finally:
        monkeypatch.undo()

    sql, params = cursor.calls[0]
    assert "user_id = %s" in sql
    assert params[0] == "u-5"
    assert params[1] == 5
    assert result[0]["brand"] == "喜茶"
    assert result[0]["photos"][0]["url"] == "https://cdn.example.com/1.jpg"


def test_delete_record_filters_by_user_id():
    cursor = _FakeCursor(
        responses=[
            {
                "one": {
                    "id": "r-7",
                }
            }
        ]
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db_module, "_pool", _FakePool(cursor))
    try:
        deleted = db_module.delete_record("u-7", "r-7")
    finally:
        monkeypatch.undo()

    sql, params = cursor.calls[0]
    assert deleted is True
    assert "DELETE FROM records" in sql
    assert "user_id = %s" in sql
    assert params == ("r-7", "u-7")


def test_delete_record_returns_false_when_not_found():
    cursor = _FakeCursor(responses=[{"one": None}])
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db_module, "_pool", _FakePool(cursor))
    try:
        deleted = db_module.delete_record("u-8", "r-missing")
    finally:
        monkeypatch.undo()

    assert deleted is False


def test_query_stats_filters_by_user_id():
    cursor = _FakeCursor(
        responses=[
            {"one": {"total_amount": Decimal("38.00"), "total_count": 2}},
            {"all": [{"brand": "喜茶", "count": 2}]},
            {"all": [{"week": "W13", "count": 2}]},
            {"all": [{"sugar": "少糖", "count": 2}]},
            {"all": [{"ice": "少冰", "count": 2}]},
            {"all": [{"day": date(2026, 3, 28), "count": 2}]},
        ]
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db_module, "_pool", _FakePool(cursor))
    try:
        result = db_module.query_stats("u-4", "month", "2026-03")
    finally:
        monkeypatch.undo()

    assert result["total_count"] == 2
    assert result["brand_dist"][0]["brand"] == "喜茶"
    assert all(params and params[0] == "u-4" for _, params in cursor.calls)


def test_query_stats_week_defaults_to_today_window():
    cursor = _FakeCursor(
        responses=[
            {"one": {"total_amount": Decimal("58.00"), "total_count": 4}},
            {"all": [{"brand": "一点点", "count": 2}]},
            {"all": [{"week": "W14", "count": 4}]},
            {"all": [{"sugar": "三分糖", "count": 2}]},
            {"all": [{"ice": "少冰", "count": 4}]},
            {"all": [{"day": date(2026, 4, 1), "count": 2}]},
        ]
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db_module, "_pool", _FakePool(cursor))
    fixed_now = datetime(2026, 4, 4, 12, 0, 0)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(db_module, "datetime", _FixedDateTime)
    try:
        result = db_module.query_stats("u-9", "week", None)
    finally:
        monkeypatch.undo()

    assert result["total_count"] == 4
    stats_sql, params = cursor.calls[0]
    assert "consumed_at >=" in stats_sql
    assert list(params) == ["u-9", date(2026, 3, 30), date(2026, 4, 6)]
