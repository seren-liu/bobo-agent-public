from typing import Literal

from fastapi import APIRouter, HTTPException
from jose import JWTError
from pydantic import BaseModel, Field

from app.core.authz import default_user_capabilities, normalize_capabilities
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from app.models.db import authenticate_user, create_user
from app.models.schemas import LoginRequest, RegisterRequest

router = APIRouter(prefix="/bobo/auth", tags=["auth"])


class AuthTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    user_id: str
    nickname: str = ""


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


def _issue_token_pair(user_id: str, nickname: str = "") -> AuthTokenResponse:
    caps = list(default_user_capabilities())
    return AuthTokenResponse(
        access_token=create_access_token(subject=user_id, extra_claims={"caps": caps}),
        refresh_token=create_refresh_token(subject=user_id, extra_claims={"caps": caps}),
        user_id=user_id,
        nickname=nickname,
    )


@router.post("/login", response_model=AuthTokenResponse)
def login(payload: LoginRequest) -> AuthTokenResponse:
    username = payload.username.strip().lower()
    user = authenticate_user(username)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid credentials")

    return _issue_token_pair(str(user["user_id"]), nickname=user.get("nickname") or "")


@router.post("/register", response_model=AuthTokenResponse)
def register(payload: RegisterRequest) -> AuthTokenResponse:
    email = payload.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=422, detail="invalid email")

    nickname = (payload.nickname.strip() or payload.name.strip())
    try:
        user = create_user(email, hash_password(payload.password), nickname=nickname)
    except ValueError as exc:
        message = str(exc)
        if message == "username already exists":
            raise HTTPException(status_code=409, detail="account already exists") from exc
        raise HTTPException(status_code=422, detail=message) from exc

    return _issue_token_pair(str(user["user_id"]), nickname=user.get("nickname") or "")


@router.post("/refresh", response_model=AuthTokenResponse)
def refresh(payload: RefreshRequest) -> AuthTokenResponse:
    try:
        token_payload = decode_token(payload.refresh_token, expected_token_type="refresh")
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="invalid refresh token") from exc

    subject = token_payload.get("sub")
    if not subject:
        raise HTTPException(status_code=401, detail="invalid refresh token")

    caps = list(normalize_capabilities(token_payload.get("caps")))
    return AuthTokenResponse(
        access_token=create_access_token(subject=str(subject), extra_claims={"caps": caps}),
        refresh_token=create_refresh_token(subject=str(subject), extra_claims={"caps": caps}),
        user_id=str(subject),
        nickname="",
    )
