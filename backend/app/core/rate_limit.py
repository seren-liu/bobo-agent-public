from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Hashable
from threading import Lock
from time import monotonic

from fastapi import HTTPException

from app.core.config import get_settings

_RATE_LIMIT_BUCKETS: dict[tuple[str, Hashable], deque[float]] = defaultdict(deque)
_RATE_LIMIT_LOCK = Lock()
_REDIS_CLIENT = None
_REDIS_IMPORT_ATTEMPTED = False


def _get_redis_client():
    global _REDIS_CLIENT, _REDIS_IMPORT_ATTEMPTED
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    if _REDIS_IMPORT_ATTEMPTED:
        return None

    _REDIS_IMPORT_ATTEMPTED = True
    try:
        from redis import Redis
    except Exception:
        return None

    try:
        _REDIS_CLIENT = Redis.from_url(get_settings().redis_url, decode_responses=False, socket_timeout=0.2, socket_connect_timeout=0.2)
    except Exception:
        _REDIS_CLIENT = None
    return _REDIS_CLIENT


def _enforce_redis_rate_limit(*, scope: str, key: Hashable, max_requests: int, window_seconds: float) -> bool:
    client = _get_redis_client()
    if client is None:
        return False

    redis_key = f"rate_limit:{scope}:{key}"
    try:
        current = client.incr(redis_key)
        if current == 1:
            client.expire(redis_key, max(1, int(window_seconds)))
        ttl = client.ttl(redis_key)
    except Exception:
        return False

    if int(current) > max_requests:
        raise HTTPException(
            status_code=429,
            detail="too many requests",
            headers={"Retry-After": str(max(1, int(ttl) if ttl and int(ttl) > 0 else int(window_seconds)))},
        )
    return True


def enforce_rate_limit(*, scope: str, key: Hashable, max_requests: int, window_seconds: float) -> None:
    """Simple in-process sliding-window rate limiter."""
    if max_requests <= 0 or window_seconds <= 0:
        return

    if _enforce_redis_rate_limit(scope=scope, key=key, max_requests=max_requests, window_seconds=window_seconds):
        return

    now = monotonic()
    bucket_key = (scope, key)

    with _RATE_LIMIT_LOCK:
        entries = _RATE_LIMIT_BUCKETS[bucket_key]
        cutoff = now - window_seconds
        while entries and entries[0] <= cutoff:
            entries.popleft()

        if len(entries) >= max_requests:
            retry_after = max(window_seconds - (now - entries[0]), 0.0) if entries else window_seconds
            raise HTTPException(
                status_code=429,
                detail="too many requests",
                headers={"Retry-After": str(max(1, int(retry_after)))},
            )

        entries.append(now)


def clear_rate_limits() -> None:
    global _REDIS_CLIENT, _REDIS_IMPORT_ATTEMPTED
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_BUCKETS.clear()
    if _REDIS_CLIENT is not None:
        try:
            _REDIS_CLIENT.close()
        except Exception:
            pass
    _REDIS_CLIENT = None
    _REDIS_IMPORT_ATTEMPTED = False
