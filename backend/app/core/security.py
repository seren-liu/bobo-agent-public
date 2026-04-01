from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


def _create_token(
    subject: str,
    *,
    token_type: str,
    expires_hours: int,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=expires_hours)).timestamp()),
        "typ": token_type,
        "jti": uuid4().hex,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    return _create_token(
        subject,
        token_type=TOKEN_TYPE_ACCESS,
        expires_hours=settings.jwt_expire_hours,
        extra_claims=extra_claims,
    )


def create_refresh_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    return _create_token(
        subject,
        token_type=TOKEN_TYPE_REFRESH,
        expires_hours=settings.jwt_refresh_expire_hours,
        extra_claims=extra_claims,
    )


def decode_token(token: str, expected_token_type: str | None = None) -> dict:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if expected_token_type and payload.get("typ") != expected_token_type:
        raise JWTError("invalid token type")
    return payload


def try_decode_token(token: str) -> dict | None:
    try:
        return decode_token(token)
    except JWTError:
        return None
