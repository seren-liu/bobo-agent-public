from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from app.core.config import get_settings
from app.services.qdrant import QdrantService

logger = logging.getLogger("bobo.memory_vectors")
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="memory-vectors")


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    future: Future[Any] = _EXECUTOR.submit(lambda: asyncio.run(coro))
    return future.result()


class MemoryVectorService:
    def __init__(self) -> None:
        self._service = QdrantService(collection_name=get_settings().memory_collection_name)

    async def _aupsert_memory_item(self, item: dict[str, Any]) -> None:
        try:
            await self._service.upsert_point(
                point_id=str(item["id"]),
                text=str(item.get("content") or ""),
                payload={
                    "memory_id": str(item["id"]),
                    "user_id": str(item.get("user_id") or ""),
                    "memory_type": str(item.get("memory_type") or ""),
                    "scope": str(item.get("scope") or ""),
                    "status": str(item.get("status") or "active"),
                    "source_kind": str(item.get("source_kind") or ""),
                    "expires_at": item.get("expires_at"),
                    "content": str(item.get("content") or ""),
                },
                keyword_fields=["user_id", "memory_type", "scope", "status"],
            )
        except Exception as exc:
            logger.warning("memory vector upsert skipped: %s", exc)

    def upsert_memory_item(self, item: dict[str, Any]) -> None:
        _run_async(self._aupsert_memory_item(item))

    async def _adelete_memory_item(self, memory_id: str) -> None:
        try:
            await self._service.delete(memory_id)
        except Exception as exc:
            logger.warning("memory vector delete skipped: %s", exc)

    def delete_memory_item(self, memory_id: str) -> None:
        _run_async(self._adelete_memory_item(memory_id))

    async def _asearch_memory_items(
        self,
        *,
        user_id: str,
        query: str,
        scope: str | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        try:
            return await self._service.search_points(
                query=query,
                top_k=top_k,
                must_match={
                    "user_id": user_id,
                    "status": "active",
                    **({"scope": scope} if scope else {}),
                },
            )
        except Exception as exc:
            logger.warning("memory vector search skipped: %s", exc)
            return []

    def search_memory_items(
        self,
        *,
        user_id: str,
        query: str,
        scope: str | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        return _run_async(
            self._asearch_memory_items(user_id=user_id, query=query, scope=scope, top_k=top_k)
        )
