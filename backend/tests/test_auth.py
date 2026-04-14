from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ["JWT_SECRET"] = "test-secret"
os.environ["MCP_SERVICE_TOKEN"] = "mcp-test-token"

from app.api import auth as auth_api  # noqa: E402
from app.core.authz import default_user_capabilities  # noqa: E402
from app.core.config import get_settings, validate_security_settings, Settings  # noqa: E402
from app.core.security import create_refresh_token, decode_token, hash_password  # noqa: E402
from app.main import _is_mcp_service_token, app  # noqa: E402


client = TestClient(app)


def test_login_returns_access_and_refresh_pair(monkeypatch):
    def _fake_authenticate(username: str):
        assert username == "dev"
        return {
            "user_id": "u-123",
            "username": "dev",
            "password_hash": hash_password("dev123456"),
        }

    monkeypatch.setattr(auth_api, "authenticate_user", _fake_authenticate)

    resp = client.post("/bobo/auth/login", json={"username": "dev", "password": "dev123456"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert data["user_id"] == "u-123"
    assert data["access_token"]
    assert data["refresh_token"]
    assert decode_token(data["access_token"], expected_token_type="access")["sub"] == "u-123"
    assert decode_token(data["refresh_token"], expected_token_type="refresh")["sub"] == "u-123"
    assert tuple(decode_token(data["access_token"], expected_token_type="access")["caps"]) == default_user_capabilities()


def test_refresh_endpoint_rotates_tokens():
    refresh_token = create_refresh_token("u-456")

    resp = client.post("/bobo/auth/refresh", json={"refresh_token": refresh_token})

    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == "u-456"
    assert decode_token(data["access_token"], expected_token_type="access")["sub"] == "u-456"
    assert decode_token(data["refresh_token"], expected_token_type="refresh")["sub"] == "u-456"
    assert tuple(decode_token(data["access_token"], expected_token_type="access")["caps"]) == default_user_capabilities()


def test_register_creates_account_and_returns_session(monkeypatch):
    def _fake_create_user(username: str, password_hash: str, nickname: str | None = None):
        assert username == "seren@example.com"
        assert password_hash
        assert nickname == "Seren"
        return {
            "user_id": "u-999",
            "username": username,
            "password_hash": password_hash,
            "nickname": nickname,
        }

    monkeypatch.setattr(auth_api, "create_user", _fake_create_user)

    resp = client.post(
        "/bobo/auth/register",
        json={"name": "Seren", "email": " Seren@example.com ", "password": "seren123456"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == "u-999"
    assert decode_token(data["access_token"], expected_token_type="access")["sub"] == "u-999"
    assert decode_token(data["refresh_token"], expected_token_type="refresh")["sub"] == "u-999"


def test_register_rejects_duplicate_account(monkeypatch):
    def _fake_create_user(username: str, password_hash: str, nickname: str | None = None):
        raise ValueError("username already exists")

    monkeypatch.setattr(auth_api, "create_user", _fake_create_user)

    resp = client.post(
        "/bobo/auth/register",
        json={"name": "Seren", "email": "seren@example.com", "password": "seren123456"},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"] == "account already exists"


def test_refresh_token_is_rejected_on_protected_routes():
    refresh_token = create_refresh_token("u-789")

    resp = client.get(
        "/bobo/records/stats?period=month",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid token"


def test_mcp_accepts_service_bearer_token():
    settings = get_settings()
    assert _is_mcp_service_token(settings.mcp_service_token, settings) is True


def test_mcp_rejects_derived_service_token_in_production():
    settings = Settings(
        env="production",
        jwt_secret="prod-secret-123",
        mcp_service_token="prod-mcp-token-123",
        metrics_access_token="prod-metrics-token",
    )

    assert _is_mcp_service_token("prod-secret-123:mcp", settings) is False
    assert _is_mcp_service_token("prod-mcp-token-123", settings) is True


def test_production_settings_reject_default_jwt_secret():
    settings = Settings(
        env="production",
        jwt_secret="change_me",
        mcp_service_token="prod-mcp-token",
        metrics_access_token="prod-metrics-token",
    )

    try:
        validate_security_settings(settings)
    except RuntimeError as exc:
        assert "JWT_SECRET" in str(exc)
    else:
        raise AssertionError("expected production settings validation to fail")


def test_production_settings_require_explicit_mcp_and_metrics_tokens():
    settings = Settings(
        env="production",
        jwt_secret="prod-secret-123",
        mcp_service_token="prod-secret-123:mcp",
        metrics_access_token="",
    )

    try:
        validate_security_settings(settings)
    except RuntimeError as exc:
        assert "MCP_SERVICE_TOKEN" in str(exc) or "METRICS_ACCESS_TOKEN" in str(exc)
    else:
        raise AssertionError("expected production settings validation to fail")
