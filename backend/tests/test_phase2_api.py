import os
from datetime import datetime

from fastapi.testclient import TestClient

os.environ["JWT_SECRET"] = "test-secret"

from app.main import _is_mcp_service_token, app  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.rate_limit import clear_rate_limits  # noqa: E402
from app.core.security import create_access_token  # noqa: E402


client = TestClient(app)


def setup_function():
    clear_rate_limits()


def _auth_header() -> dict[str, str]:
    token = create_access_token("u-test")
    return {"Authorization": f"Bearer {token}"}


def test_health_ok():
    resp = client.get("/bobo/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_confirm_records_201():
    payload = {
        "items": [
            {
                "brand": "喜茶",
                "name": "多肉葡萄",
                "size": "大",
                "sugar": "少糖",
                "ice": "少冰",
                "mood": "开心",
                "price": 19,
                "source": "manual",
                "consumed_at": datetime.utcnow().isoformat() + "Z",
            }
        ],
    }
    resp = client.post("/bobo/records/confirm", json=payload, headers=_auth_header())
    assert resp.status_code == 201
    data = resp.json()
    assert data["inserted"] == 1
    assert data["records"][0]["brand"] == "喜茶"
    assert data["records"][0]["mood"] == "开心"


def test_stats_json_shape():
    resp = client.get("/bobo/records/stats?period=month&date=2025-06", headers=_auth_header())
    assert resp.status_code == 200
    data = resp.json()
    assert "total_amount" in data
    assert "brand_dist" in data


def test_mcp_service_token_bypasses_general_jwt_guard():
    settings = get_settings()
    expected = settings.mcp_service_token or "test-secret:mcp"
    assert _is_mcp_service_token(expected, settings) is True


def test_login_rate_limit(monkeypatch):
    from app.api import auth as auth_api

    calls = {"count": 0}

    def _fake_limit(**kwargs):
        calls["count"] += 1
        if calls["count"] > 1:
            raise auth_api.HTTPException(status_code=429, detail="too many requests")

    monkeypatch.setattr(auth_api, "enforce_rate_limit", _fake_limit)

    resp1 = client.post("/bobo/auth/login", json={"username": "none@example.com", "password": "wrong"})
    resp2 = client.post("/bobo/auth/login", json={"username": "none@example.com", "password": "wrong"})

    assert resp1.status_code in {401, 429}
    assert resp2.status_code == 429
