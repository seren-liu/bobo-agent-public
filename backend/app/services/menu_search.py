from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from functools import lru_cache
from threading import Lock
from time import monotonic
from typing import Any, Protocol

from app.core.brands import canonicalize_brand_name
from app.core.config import Settings, get_settings
from app.observability import observe_menu_search
from app.services.qdrant import QdrantService

logger = logging.getLogger("bobo.menu_search")


class MenuSearchCacheBackend(Protocol):
    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any, ttl_seconds: float) -> None: ...

    def incr(self, key: str, ttl_seconds: float) -> int: ...

    def get_version(self, namespace: str) -> int: ...

    def bump_version(self, namespace: str) -> int: ...


class MemoryMenuSearchCacheBackend:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, Any]] = {}
        self._versions: defaultdict[str, int] = defaultdict(int)
        self._lock = Lock()

    def get(self, key: str) -> Any | None:
        now = monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        expires_at = monotonic() + max(ttl_seconds, 0.1)
        with self._lock:
            self._entries[key] = (expires_at, value)

    def incr(self, key: str, ttl_seconds: float) -> int:
        now = monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry[0] <= now:
                count = 1
            else:
                count = int(entry[1]) + 1
            self._entries[key] = (now + max(ttl_seconds, 0.1), count)
            return count

    def get_version(self, namespace: str) -> int:
        with self._lock:
            return int(self._versions[namespace])

    def bump_version(self, namespace: str) -> int:
        with self._lock:
            self._versions[namespace] += 1
            return int(self._versions[namespace])


class RedisMenuSearchCacheBackend:
    def __init__(self, redis_url: str) -> None:
        try:
            from redis import Redis
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("redis package is required") from exc

        self._client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=0.2,
            socket_connect_timeout=0.2,
        )

    def get(self, key: str) -> Any | None:
        try:
            raw = self._client.get(key)
        except Exception:
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        try:
            self._client.setex(
                key,
                max(1, int(ttl_seconds)),
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            )
        except Exception:
            return

    def incr(self, key: str, ttl_seconds: float) -> int:
        try:
            current = int(self._client.incr(key))
            if current == 1:
                self._client.expire(key, max(1, int(ttl_seconds)))
            return current
        except Exception:
            return 0

    def get_version(self, namespace: str) -> int:
        try:
            raw = self._client.get(namespace)
        except Exception:
            return 0
        try:
            return int(raw) if raw is not None else 0
        except Exception:
            return 0

    def bump_version(self, namespace: str) -> int:
        try:
            return int(self._client.incr(namespace))
        except Exception:
            return 0


_MENU_SEARCH_CACHE_BACKEND: MenuSearchCacheBackend | None = None
_MENU_SEARCH_CACHE_BACKEND_LOCK = Lock()
_MENU_SEARCH_CACHE_NAMESPACE = "menu_search:version"


def get_menu_search_cache_backend() -> MenuSearchCacheBackend:
    global _MENU_SEARCH_CACHE_BACKEND
    if _MENU_SEARCH_CACHE_BACKEND is not None:
        return _MENU_SEARCH_CACHE_BACKEND

    with _MENU_SEARCH_CACHE_BACKEND_LOCK:
        if _MENU_SEARCH_CACHE_BACKEND is not None:
            return _MENU_SEARCH_CACHE_BACKEND
        settings = get_settings()
        try:
            _MENU_SEARCH_CACHE_BACKEND = RedisMenuSearchCacheBackend(settings.redis_url)
        except Exception:
            _MENU_SEARCH_CACHE_BACKEND = MemoryMenuSearchCacheBackend()
        return _MENU_SEARCH_CACHE_BACKEND


def invalidate_menu_search_cache() -> None:
    backend = get_menu_search_cache_backend()
    version = backend.bump_version(_MENU_SEARCH_CACHE_NAMESPACE)
    logger.info(
        json.dumps(
            {"event": "menu_search_cache_invalidate", "version": version},
            ensure_ascii=False,
            default=str,
        )
    )


def _normalize_menu_query(query: str) -> str:
    normalized = re.sub(r"\s+", "", query or "")
    for token in ("给我", "推荐", "一下", "一杯", "来一杯", "想喝", "请", "喝什么"):
        normalized = normalized.replace(token, "")
    normalized = normalized.replace("的", "")
    return normalized.strip("的呀吧呢吗，。！ ")


def _normalize_cache_lookup_key(query: str) -> str:
    normalized = _normalize_menu_query(query)
    normalized = re.sub(r"[，。！？!?、,.]+", "", normalized)
    return normalized.lower()


def _category_aliases(query: str) -> list[str]:
    aliases = [
        ("水果茶", ["水果茶", "果茶", "鲜果茶", "果饮"]),
        ("鲜果茶", ["鲜果茶", "水果茶", "果茶", "果饮"]),
        ("果茶", ["果茶", "水果茶", "鲜果茶", "果饮"]),
        ("轻乳茶", ["轻乳茶", "奶茶", "乳茶"]),
        ("柠檬茶", ["柠檬茶", "果茶", "鲜果茶"]),
        ("奶茶", ["奶茶", "牛乳茶", "乳茶", "厚乳"]),
        ("纯茶", ["纯茶", "茗茶", "原叶茶", "乌龙茶", "绿茶"]),
        ("咖啡", ["咖啡", "拿铁", "美式"]),
    ]
    for key, values in aliases:
        if key in query:
            return values
    return []


def _build_query_candidates_uncached(query: str) -> list[str]:
    normalized = _normalize_menu_query(query)
    candidates: list[str] = []
    if normalized:
        candidates.append(normalized)
    candidates.extend(_category_aliases(normalized))
    for texture in ("清爽", "清新", "解腻", "果香"):
        if texture in normalized:
            stripped = normalized.replace(texture, "").strip()
            if stripped:
                candidates.append(stripped)
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _query_bonus(item: dict[str, object], raw_query: str) -> float:
    text = f"{item.get('name') or ''} {item.get('description') or ''}"
    bonus = 0.0
    if "清爽" in raw_query and any(token in text for token in ("清爽", "清新", "解腻", "爽")):
        bonus += 0.25
    if any(token in raw_query for token in ("水果茶", "果茶")):
        if item.get("drink_category") == "fruit_tea":
            bonus += 0.3
        elif item.get("item_type") == "drink":
            bonus += 0.06
        else:
            bonus -= 0.3
    return bonus


def _merge_search_results(result_sets: list[list[dict[str, object]]], raw_query: str, top_k: int) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for result_set in result_sets:
        for item in result_set:
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            score = float(item.get("score") or 0.0) + _query_bonus(item, raw_query)
            candidate = {**item, "score": score}
            current = merged.get(item_id)
            if current is None or score > float(current.get("score") or 0.0):
                merged[item_id] = candidate

    out = list(merged.values())
    out.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            0 if item.get("item_type") == "drink" else 1,
            float(item.get("price") or 9999),
            str(item.get("name") or ""),
        )
    )
    return out[:top_k]


class MenuSearchService:
    def __init__(
        self,
        qdrant_service: QdrantService | None = None,
        cache_backend: MenuSearchCacheBackend | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._qdrant = qdrant_service or QdrantService()
        self._cache = cache_backend or get_menu_search_cache_backend()
        self._settings = settings or get_settings()

    def build_query_candidates(self, query: str) -> list[str]:
        lookup_key = _normalize_cache_lookup_key(query)
        if not lookup_key:
            return [query] if query else []

        cache_key = f"menu_search:norm:v1:{lookup_key}"
        cached = self._cache.get(cache_key)
        if isinstance(cached, list) and cached:
            return [str(item) for item in cached if str(item).strip()]

        counter_key = f"menu_search:norm_hits:v1:{lookup_key}"
        count = self._cache.incr(counter_key, self._settings.menu_search_query_cache_ttl_seconds)
        candidates = _build_query_candidates_uncached(query)
        if not candidates:
            candidates = [query]
        if count >= max(int(self._settings.menu_search_hot_query_threshold), 1):
            self._cache.set(cache_key, candidates, self._settings.menu_search_query_cache_ttl_seconds)
        return candidates

    async def search(
        self,
        *,
        query: str,
        brand: str | None = None,
        top_k: int = 5,
        source: str = "api",
    ) -> list[dict[str, object]]:
        raw_query = query or ""
        normalized_brand = canonicalize_brand_name(brand)
        candidates = self.build_query_candidates(raw_query)
        if not candidates:
            candidates = [raw_query]

        version = self._cache.get_version(_MENU_SEARCH_CACHE_NAMESPACE)
        cache_key = json.dumps(
            {
                "kind": "menu_search_result",
                "v": version,
                "brand": normalized_brand,
                "top_k": top_k,
                "query": _normalize_cache_lookup_key(raw_query),
                "candidates": candidates[:5],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        cached = self._cache.get(cache_key)
        if isinstance(cached, list):
            observe_menu_search(source=source, brand_filter=bool(normalized_brand), outcome="cache_hit", result_count=len(cached))
            return cached

        pool_limit = min(max(top_k * 2, top_k), 20)
        try:
            result_sets = await asyncio.gather(
                *(self._qdrant.search(query=candidate, brand=normalized_brand, top_k=pool_limit) for candidate in candidates[:5])
            )
            results = _merge_search_results(result_sets, raw_query, top_k)
            self._cache.set(cache_key, results, self._settings.menu_search_result_cache_ttl_seconds)
            observe_menu_search(source=source, brand_filter=bool(normalized_brand), outcome="success", result_count=len(results))
            return results
        except Exception:
            observe_menu_search(source=source, brand_filter=bool(normalized_brand), outcome="failure", result_count=0)
            raise


@lru_cache(maxsize=1)
def get_menu_search_service() -> MenuSearchService:
    return MenuSearchService()
