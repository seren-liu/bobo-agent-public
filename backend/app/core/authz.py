from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TypedDict


BASIC_USER_CAPABILITIES = (
    "records:write",
    "menu:search",
    "stats:read",
    "calendar:read",
)
MENU_ADMIN_CAPABILITY = "menu:admin"

TOOL_CAPABILITY_MAP = {
    "record_drink": "records:write",
    "search_menu": "menu:search",
    "get_stats": "stats:read",
    "get_recent_records": "stats:read",
    "get_day": "stats:read",
    "get_calendar": "calendar:read",
    "update_menu": MENU_ADMIN_CAPABILITY,
}


class AuthContext(TypedDict, total=False):
    user_id: str
    auth_source: str
    source: str
    capabilities: tuple[str, ...]


_auth_context: ContextVar[AuthContext | None] = ContextVar("bobo_auth_context", default=None)


def set_auth_context(context: AuthContext) -> Token[AuthContext | None]:
    return _auth_context.set(context)


def reset_auth_context(token: Token[AuthContext | None]) -> None:
    _auth_context.reset(token)


def get_auth_context() -> AuthContext | None:
    return _auth_context.get()


def default_user_capabilities() -> tuple[str, ...]:
    return BASIC_USER_CAPABILITIES


def normalize_capabilities(raw_caps: object | None) -> tuple[str, ...]:
    if raw_caps is None:
        return default_user_capabilities()
    if isinstance(raw_caps, str):
        values = [raw_caps]
    elif isinstance(raw_caps, (list, tuple, set)):
        values = [str(item).strip() for item in raw_caps if str(item).strip()]
    else:
        values = []
    merged = [*default_user_capabilities(), *values]
    return tuple(dict.fromkeys(merged))


def has_capability(tool_name: str, capabilities: tuple[str, ...]) -> bool:
    if "*" in capabilities:
        return True
    required = TOOL_CAPABILITY_MAP.get(tool_name)
    if not required:
        return True
    return required in set(capabilities)
