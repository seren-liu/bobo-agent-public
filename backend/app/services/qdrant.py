from __future__ import annotations

import json
import logging
import os
import time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5, NAMESPACE_URL

from app.services.embedding import EmbeddingService

logger = logging.getLogger("bobo.qdrant")


class QdrantService:
    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        collection_name: str = "menu_vectors",
        embedding_service: EmbeddingService | None = None,
        client: Any | None = None,
        models: Any | None = None,
    ):
        self.collection_name = collection_name
        self._url = url or os.getenv("QDRANT_URL") or "http://localhost:6333"
        self._api_key = api_key or os.getenv("QDRANT_API_KEY")
        self._embedding = embedding_service or EmbeddingService()
        self._client = client
        self._models = models
        self._initialized = False

    def _default_payload_indexes(self) -> list[tuple[str, Any]]:
        models = self._get_models()
        if self.collection_name != "menu_vectors":
            return []
        return [
            ("brand", models.PayloadSchemaType.KEYWORD),
            ("is_active", models.PayloadSchemaType.BOOL),
        ]

    @staticmethod
    def _normalize_point_id(point_id: str | int) -> str | int:
        if isinstance(point_id, int):
            return point_id
        raw = str(point_id).strip()
        if raw.isdigit():
            return int(raw)
        try:
            return str(UUID(raw))
        except ValueError:
            return str(uuid5(NAMESPACE_URL, f"bobo-qdrant:{raw}"))

    @property
    def client(self) -> Any:
        return self._get_client()

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            from qdrant_client import AsyncQdrantClient
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("qdrant-client package is required") from exc

        self._client = AsyncQdrantClient(url=self._url, api_key=self._api_key)
        return self._client

    def _get_models(self) -> Any:
        if self._models is not None:
            return self._models

        try:
            from qdrant_client.http import models
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("qdrant-client package is required") from exc

        self._models = models
        return self._models

    def _extract_vector_size(self, collection_info: Any) -> int | None:
        try:
            vectors = collection_info.config.params.vectors
        except Exception:
            return None

        size = getattr(vectors, "size", None)
        if isinstance(size, int):
            return size
        if isinstance(vectors, dict):
            first = next(iter(vectors.values()), None)
            nested_size = getattr(first, "size", None)
            if isinstance(nested_size, int):
                return nested_size
        return None

    async def init_collection(self) -> None:
        if self._initialized:
            return

        start = time.perf_counter()
        client = self._get_client()
        models = self._get_models()

        exists = await client.collection_exists(self.collection_name)
        if not exists:
            await client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(size=self._embedding.vector_size(), distance=models.Distance.COSINE),
            )
        elif hasattr(client, "get_collection"):
            existing = await client.get_collection(self.collection_name)
            existing_size = self._extract_vector_size(existing)
            expected_size = self._embedding.vector_size()
            if existing_size is not None and existing_size != expected_size:
                raise RuntimeError(
                    f"Qdrant collection '{self.collection_name}' vector size mismatch: "
                    f"existing={existing_size}, expected={expected_size}. Recreate collection and reseed vectors."
                )

        for field_name, field_schema in self._default_payload_indexes():
            await client.create_payload_index(
                collection_name=self.collection_name,
                field_name=field_name,
                field_schema=field_schema,
            )

        self._initialized = True
        logger.info(
            json.dumps(
                {
                    "event": "qdrant_init_collection",
                    "collection": self.collection_name,
                    "created": not exists,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
                ensure_ascii=False,
                default=str,
            )
        )

    async def upsert(
        self,
        menu_id: str,
        brand: str,
        name: str,
        price: float,
        size: str,
        description: str | None,
        is_active: bool,
    ) -> None:
        start = time.perf_counter()
        await self.init_collection()

        models = self._get_models()
        vector = await self._embedding.embed_text(
            " ".join(part for part in [brand, name, description or ""] if part),
            text_type="document",
        )
        payload = {
            "id": menu_id,
            "brand": brand,
            "name": name,
            "price": float(price) if isinstance(price, (int, float, Decimal)) else 0.0,
            "size": size,
            "description": description or "",
            "is_active": is_active,
        }
        point = models.PointStruct(id=self._normalize_point_id(menu_id), vector=vector, payload=payload)

        await self._get_client().upsert(
            collection_name=self.collection_name,
            points=[point],
            wait=True,
        )
        logger.info(
            json.dumps(
                {
                    "event": "qdrant_upsert",
                    "collection": self.collection_name,
                    "menu_id": menu_id,
                    "brand": brand,
                    "name": name,
                    "is_active": is_active,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
                ensure_ascii=False,
                default=str,
            )
        )

    async def upsert_point(
        self,
        *,
        point_id: str,
        text: str,
        payload: dict[str, Any],
        keyword_fields: list[str] | None = None,
    ) -> None:
        await self.init_collection()
        models = self._get_models()
        vector = await self._embedding.embed_text(text, text_type="document")
        point = models.PointStruct(id=self._normalize_point_id(point_id), vector=vector, payload=payload)
        await self._get_client().upsert(collection_name=self.collection_name, points=[point], wait=True)
        for field_name in keyword_fields or []:
            try:
                await self._get_client().create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field_name,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            except Exception:
                continue

    async def search_points(
        self,
        *,
        query: str,
        top_k: int = 5,
        must_match: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        await self.init_collection()
        models = self._get_models()
        query_vector = await self._embedding.embed_text(query, text_type="query")

        must: list[Any] = []
        for key, value in (must_match or {}).items():
            if value is None:
                continue
            must.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))

        client = self._get_client()
        if hasattr(client, "search"):
            points = await client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                query_filter=models.Filter(must=must) if must else None,
                limit=top_k,
                with_payload=True,
            )
        else:
            result = await client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=models.Filter(must=must) if must else None,
                limit=top_k,
                with_payload=True,
            )
            points = getattr(result, "points", []) or []

        return [
            {
                "id": str((point.payload or {}).get("memory_id") or (point.payload or {}).get("id") or point.id),
                "payload": point.payload or {},
                "score": float(point.score),
            }
            for point in points
        ]

    async def search(self, query: str, brand: str | None = None, top_k: int = 5) -> list[dict]:
        start = time.perf_counter()
        await self.init_collection()

        models = self._get_models()
        query_vector = await self._embedding.embed_text(query, text_type="query")

        must = [
            models.FieldCondition(key="is_active", match=models.MatchValue(value=True)),
        ]
        if brand:
            must.append(models.FieldCondition(key="brand", match=models.MatchValue(value=brand)))

        client = self._get_client()
        if hasattr(client, "search"):
            points = await client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                query_filter=models.Filter(must=must),
                limit=top_k,
                with_payload=True,
            )
        else:
            # qdrant-client >= 1.17 removes `search` in favor of `query_points`.
            result = await client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=models.Filter(must=must),
                limit=top_k,
                with_payload=True,
            )
            points = getattr(result, "points", []) or []

        out: list[dict] = []
        for point in points:
            payload = point.payload or {}
            out.append(
                {
                    "id": str(payload.get("id") or point.id),
                    "brand": payload.get("brand"),
                    "name": payload.get("name"),
                    "size": payload.get("size"),
                    "price": payload.get("price"),
                    "description": payload.get("description"),
                    "score": float(point.score),
                }
            )
        logger.info(
            json.dumps(
                {
                    "event": "qdrant_search",
                    "collection": self.collection_name,
                    "brand": brand,
                    "top_k": top_k,
                    "query_len": len(query),
                    "result_count": len(out),
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return out

    async def delete(self, menu_id: str) -> None:
        start = time.perf_counter()
        await self.init_collection()

        models = self._get_models()
        await self._get_client().delete(
            collection_name=self.collection_name,
            points_selector=models.PointIdsList(points=[self._normalize_point_id(menu_id)]),
            wait=True,
        )
        logger.info(
            json.dumps(
                {
                    "event": "qdrant_delete",
                    "collection": self.collection_name,
                    "menu_id": menu_id,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
                ensure_ascii=False,
                default=str,
            )
        )
