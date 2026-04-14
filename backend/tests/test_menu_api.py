from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token


client = TestClient(app)


def _auth_header() -> dict[str, str]:
    response = client.post(
        "/bobo/auth/register",
        json={
            "email": "menu_api_test@example.com",
            "password": "Passw0rd!123",
            "nickname": "MenuApi",
            "name": "MenuApi",
        },
    )
    if response.status_code not in {200, 409}:
        raise AssertionError(response.text)
    if response.status_code == 409:
        response = client.post(
            "/bobo/auth/login",
            json={"username": "menu_api_test@example.com", "password": "Passw0rd!123"},
        )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _admin_auth_header() -> dict[str, str]:
    token = create_access_token("u-menu-admin", extra_claims={"caps": ["menu:admin"]})
    return {"Authorization": f"Bearer {token}"}


def test_menu_search_uses_multi_candidate_merge(monkeypatch):
    captured = {}

    class _FakeService:
        async def search(self, query, brand=None, top_k=5, source="api"):
            captured["query"] = query
            captured["brand"] = brand
            captured["top_k"] = top_k
            captured["source"] = source
            return [
                {
                    "id": "a",
                    "brand": "喜茶",
                    "name": "清爽芭乐提",
                    "price": 19.0,
                    "description": "清爽鲜果茶",
                    "item_type": "drink",
                    "drink_category": "fruit_tea",
                    "score": 0.7,
                }
            ]

    monkeypatch.setattr("app.api.menu.get_menu_search", lambda: _FakeService())

    response = client.get("/bobo/menu/search?q=清爽的水果茶&top_k=5", headers=_auth_header())

    assert response.status_code == 200
    body = response.json()
    assert captured == {"query": "清爽的水果茶", "brand": None, "top_k": 5, "source": "api"}
    assert body["results"][0]["id"] == "a"
    assert body["results"][0]["item_type"] == "drink"
    assert body["results"][0]["drink_category"] == "fruit_tea"


def test_menu_mutations_require_admin_capability():
    create_resp = client.post("/bobo/menu", json={"brand": "喜茶", "name": "多肉葡萄"}, headers=_auth_header())
    assert create_resp.status_code == 403

    update_resp = client.put("/bobo/menu/menu-1", json={"name": "改名"}, headers=_auth_header())
    assert update_resp.status_code == 403

    delete_resp = client.delete("/bobo/menu/menu-1", headers=_auth_header())
    assert delete_resp.status_code == 403


def test_menu_create_allows_admin_capability(monkeypatch):
    class _FakeService:
        async def add_item(self, payload):
            return {"item": {"id": "menu-1", "brand": payload["brand"], "name": payload["name"]}}

    monkeypatch.setattr("app.api.menu.get_menu_ops_service", lambda: _FakeService())

    response = client.post("/bobo/menu", json={"brand": "喜茶", "name": "多肉葡萄"}, headers=_admin_auth_header())

    assert response.status_code == 201
    assert response.json()["id"] == "menu-1"
