from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.menu_search import MemoryMenuSearchCacheBackend, MenuSearchService


def _settings(**overrides):
    defaults = {
        "menu_search_result_cache_ttl_seconds": 90.0,
        "menu_search_query_cache_ttl_seconds": 600.0,
        "menu_search_hot_query_threshold": 2,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_menu_search_service_caches_hot_query_normalization(monkeypatch):
    build_calls: list[str] = []

    def _fake_build(query: str) -> list[str]:
        build_calls.append(query)
        return ["清爽水果茶", "水果茶"]

    monkeypatch.setattr("app.services.menu_search._build_query_candidates_uncached", _fake_build)

    service = MenuSearchService(
        qdrant_service=object(),
        cache_backend=MemoryMenuSearchCacheBackend(),
        settings=_settings(),
    )

    assert service.build_query_candidates("清爽的水果茶") == ["清爽水果茶", "水果茶"]
    assert service.build_query_candidates("清爽 的 水果茶") == ["清爽水果茶", "水果茶"]
    assert service.build_query_candidates("清爽，的水果茶！") == ["清爽水果茶", "水果茶"]
    assert build_calls == ["清爽的水果茶", "清爽 的 水果茶"]


def test_menu_search_service_caches_results_for_equivalent_queries():
    calls: list[tuple[str, str | None, int]] = []

    class _FakeQdrant:
        async def search(self, query, brand=None, top_k=5):
            calls.append((query, brand, top_k))
            return [
                {
                    "id": "drink-1",
                    "brand": brand or "喜茶",
                    "name": "清爽芭乐提",
                    "price": 19.0,
                    "description": "清爽鲜果茶",
                    "item_type": "drink",
                    "drink_category": "fruit_tea",
                    "score": 0.7 if query == "清爽水果茶" else 0.45,
                }
            ]

    service = MenuSearchService(
        qdrant_service=_FakeQdrant(),
        cache_backend=MemoryMenuSearchCacheBackend(),
        settings=_settings(),
    )

    first = asyncio.run(service.search(query="清爽的水果茶", brand="喜茶", top_k=5, source="api"))
    second = asyncio.run(service.search(query="清爽 的 水果茶", brand="喜茶", top_k=5, source="api"))

    assert first == second
    assert calls == [
        ("清爽水果茶", "喜茶", 10),
        ("水果茶", "喜茶", 10),
        ("果茶", "喜茶", 10),
        ("鲜果茶", "喜茶", 10),
        ("果饮", "喜茶", 10),
    ]


def test_menu_search_service_invalidates_result_cache_when_version_changes():
    calls: list[str] = []
    cache_backend = MemoryMenuSearchCacheBackend()

    class _FakeQdrant:
        async def search(self, query, brand=None, top_k=5):
            calls.append(query)
            return [
                {
                    "id": "drink-1",
                    "brand": brand or "喜茶",
                    "name": "多肉葡萄",
                    "price": 19.0,
                    "description": "葡萄果茶",
                    "item_type": "drink",
                    "drink_category": "fruit_tea",
                    "score": 0.8,
                }
            ]

    service = MenuSearchService(
        qdrant_service=_FakeQdrant(),
        cache_backend=cache_backend,
        settings=_settings(),
    )

    first = asyncio.run(service.search(query="葡萄", brand="喜茶", top_k=5, source="api"))
    cache_backend.bump_version("menu_search:version")
    second = asyncio.run(service.search(query="葡萄", brand="喜茶", top_k=5, source="api"))

    assert first == second
    assert calls == ["葡萄", "葡萄"]
