from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable
from decimal import Decimal
from math import log
from typing import Any
from uuid import UUID, uuid5, NAMESPACE_URL

import psycopg
from psycopg.rows import dict_row

from app.core.brands import canonicalize_brand_name
from app.core.config import get_settings, to_psycopg_conninfo
from app.core.resilience import DependencyError, call_with_resilience, get_circuit_breaker
from app.observability import observe_qdrant_search
from app.services.embedding import EmbeddingService
from app.services.menu_typing import infer_menu_taxonomy

logger = logging.getLogger("bobo.qdrant")

_MENU_CATEGORY_TERMS = (
    "奶茶",
    "牛乳茶",
    "乳茶",
    "厚乳",
    "奶香",
    "果茶",
    "鲜果茶",
    "水果茶",
    "果饮",
    "果香",
    "轻乳茶",
    "纯茶",
    "茗茶",
    "原叶茶",
    "柠檬茶",
    "柠檬",
    "咖啡",
    "拿铁",
    "美式",
)
_INTENT_TERMS = ("经典", "招牌", "top3", "top5", "前三", "热门", "人气", "推荐")
_DEFAULT_RRF_K = 60
_DEFAULT_SPARSE_SCAN_LIMIT = 1200
_ASCII_TOKEN_PATTERN = re.compile(r"[a-z0-9]{2,}")
_HAN_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
_STOP_TERMS = ("的", "了", "呢", "呀", "啊", "请", "给我", "来一杯", "一杯", "一下", "推荐")
_NON_DRINK_TERMS = (
    "薯片",
    "零食",
    "饼干",
    "周边",
    "礼品卡",
    "礼包",
    "纸袋",
    "徽章",
    "杯子",
    "吸管",
)
_PACKAGED_TERMS = (
    "保质期",
    "净含量",
    "规格",
    "盒",
    "袋",
)
_TEXTURE_TERMS = ("清爽", "清新", "解腻", "爽口", "轻盈", "不腻", "酸甜", "果香", "鲜")

class QdrantService:
    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        collection_name: str = "menu_vectors",
        embedding_service: EmbeddingService | None = None,
        client: Any | None = None,
        models: Any | None = None,
        sparse_document_provider: Callable[..., list[dict[str, Any]]] | None = None,
    ):
        self.collection_name = collection_name
        self._url = url or os.getenv("QDRANT_URL") or "http://localhost:6333"
        self._api_key = api_key or os.getenv("QDRANT_API_KEY")
        self._embedding = embedding_service or EmbeddingService()
        self._client = client
        self._models = models
        self._initialized = False
        self._sparse_document_provider = sparse_document_provider

    def _default_payload_indexes(self) -> list[tuple[str, Any]]:
        models = self._get_models()
        if self.collection_name != "menu_vectors":
            return []
        return [
            ("brand", models.PayloadSchemaType.KEYWORD),
            ("is_active", models.PayloadSchemaType.BOOL),
            ("item_type", models.PayloadSchemaType.KEYWORD),
            ("drink_category", models.PayloadSchemaType.KEYWORD),
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

    @staticmethod
    def _normalize_query_text(value: str) -> str:
        return re.sub(r"\s+", "", value or "").lower()

    def _meaningful_query_terms(self, query: str) -> list[str]:
        normalized = self._normalize_query_text(query)
        if not normalized:
            return []

        terms: list[str] = []
        for token in _MENU_CATEGORY_TERMS + _INTENT_TERMS + _TEXTURE_TERMS:
            if token and token in normalized and token not in terms:
                terms.append(token)

        cleaned = normalized
        for token in _STOP_TERMS:
            cleaned = cleaned.replace(token, "")

        for block in _HAN_TOKEN_PATTERN.findall(cleaned):
            if len(block) <= 1:
                continue
            if block not in terms:
                terms.append(block)
            upper = min(len(block), 4)
            lower = 2 if len(block) >= 2 else len(block)
            for size in range(upper, lower - 1, -1):
                for idx in range(0, len(block) - size + 1):
                    piece = block[idx : idx + size]
                    if piece in _STOP_TERMS or len(piece.strip()) <= 1:
                        continue
                    if piece not in terms:
                        terms.append(piece)
        return terms[:32]

    def _prepare_search_query(self, query: str) -> str:
        normalized = self._normalize_query_text(query)
        if not normalized:
            return query
        cleaned = normalized
        for token in _STOP_TERMS:
            cleaned = cleaned.replace(token, "")
        return cleaned or normalized

    def _looks_like_non_drink(self, item: dict[str, Any]) -> bool:
        text = self._normalize_query_text(f"{item.get('name') or ''} {item.get('description') or ''}")
        return any(term in text for term in _NON_DRINK_TERMS)

    def _looks_like_packaged_goods(self, item: dict[str, Any]) -> bool:
        text = self._normalize_query_text(f"{item.get('name') or ''} {item.get('description') or ''}")
        return any(term in text for term in _PACKAGED_TERMS)

    def _lexical_score(self, query: str, item: dict[str, Any]) -> float:
        query_text = self._normalize_query_text(query)
        item_text = self._normalize_query_text(f"{item.get('name') or ''} {item.get('description') or ''}")
        score = 0.0

        query_terms = self._meaningful_query_terms(query)
        category_terms = [term for term in _MENU_CATEGORY_TERMS if term in query_text]

        for term in query_terms:
            if term and term in item_text:
                if term in _TEXTURE_TERMS:
                    score += 0.38
                elif term in _MENU_CATEGORY_TERMS:
                    score += 0.45
                else:
                    score += 0.3

        if any(term in query_text for term in _INTENT_TERMS):
            if any(term in item_text for term in ("经典", "招牌", "热门", "人气", "爆款")):
                score += 0.25

        brand = str(item.get("brand") or "")
        if brand and brand.lower() in query_text:
            score += 0.2

        name = str(item.get("name") or "")
        if name and name.lower() in query_text:
            score += 0.45

        if category_terms and (
            str(item.get("item_type") or "") not in {"", "drink"} or self._looks_like_non_drink(item)
        ):
            score -= 0.9

        return score

    def _combined_score(self, query: str, point: Any) -> float:
        payload = point.payload or {}
        dense_score = float(point.score)
        lexical_score = self._lexical_score(query, payload)
        return dense_score + lexical_score

    def _tokenize_sparse_query(self, query: str) -> list[str]:
        normalized = self._normalize_query_text(query)
        if not normalized:
            return []

        tokens: list[str] = list(self._meaningful_query_terms(query))

        for token in _ASCII_TOKEN_PATTERN.findall(normalized):
            if token not in tokens:
                tokens.append(token)

        cleaned = normalized
        for stop in _STOP_TERMS:
            cleaned = cleaned.replace(stop, "")
        if cleaned and cleaned not in tokens:
            tokens.append(cleaned)
        if normalized not in tokens:
            tokens.append(normalized)
        return tokens[:24]

    def _menu_text(self, item: dict[str, Any]) -> str:
        return self._normalize_query_text(
            " ".join(
                str(part or "")
                for part in (item.get("brand"), item.get("name"), item.get("description"))
                if str(part or "").strip()
            )
        )

    def _load_sparse_documents(self, *, brand: str | None, limit: int) -> list[dict[str, Any]]:
        if self.collection_name != "menu_vectors":
            return []

        if self._sparse_document_provider is not None:
            return list(self._sparse_document_provider(brand=brand, limit=limit) or [])

        database_url = get_settings().database_url
        if not database_url:
            return []

        sql = """
        SELECT id::text AS id, brand, name, size, price, description, item_type, drink_category
        FROM menu
        WHERE is_active = TRUE
        """
        params: list[Any] = []
        if brand:
            sql += " AND brand = %s"
            params.append(brand)
        sql += " ORDER BY brand, name LIMIT %s"
        params.append(limit)
        database_url = to_psycopg_conninfo(database_url)

        try:
            with psycopg.connect(database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
                cur.execute(sql, params)
                rows = list(cur.fetchall() or [])
                enriched: list[dict[str, Any]] = []
                for row in rows:
                    item = dict(row)
                    if not item.get("item_type"):
                        item.update(infer_menu_taxonomy(item))
                    enriched.append(item)
                return enriched
        except Exception:
            return []

    def _bm25_sparse_search(self, *, query: str, documents: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
        tokens = self._tokenize_sparse_query(query)
        if not tokens or not documents:
            return []

        prepared: list[tuple[dict[str, Any], str]] = []
        doc_freq: dict[str, int] = {token: 0 for token in tokens}
        lengths: list[int] = []
        for item in documents:
            text = self._menu_text(item)
            if not text:
                continue
            prepared.append((item, text))
            lengths.append(max(len(text), 1))
            for token in tokens:
                if token in text:
                    doc_freq[token] += 1

        if not prepared:
            return []

        avgdl = sum(lengths) / len(lengths)
        k1 = 1.2
        b = 0.75
        results: list[dict[str, Any]] = []
        corpus_size = len(prepared)

        for item, text in prepared:
            doc_len = max(len(text), 1)
            score = 0.0
            for token in tokens:
                tf = text.count(token)
                if tf <= 0:
                    continue
                df = doc_freq.get(token, 0)
                idf = log(1 + (corpus_size - df + 0.5) / (df + 0.5))
                denom = tf + k1 * (1 - b + b * (doc_len / max(avgdl, 1)))
                score += idf * ((tf * (k1 + 1)) / max(denom, 1e-9))

            if score <= 0:
                continue
            results.append(
                {
                    "id": str(item.get("id") or ""),
                    "brand": item.get("brand"),
                    "name": item.get("name"),
                    "size": item.get("size"),
                    "price": item.get("price"),
                    "description": item.get("description"),
                    "item_type": item.get("item_type"),
                    "drink_category": item.get("drink_category"),
                    "score": score,
                    "dense_score": 0.0,
                    "sparse_score": score,
                    "rrf_score": 0.0,
                    "lexical_score": self._lexical_score(query, item),
                }
            )

        results.sort(
            key=lambda item: (
                -float(item.get("sparse_score") or 0.0),
                -float(item.get("lexical_score") or 0.0),
                str(item.get("name") or ""),
            )
        )
        return results[:top_k]

    def _keyword_fallback_search(self, *, query: str, documents: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
        normalized_query = self._normalize_query_text(query)
        if not normalized_query or not documents:
            return []

        query_tokens = self._tokenize_sparse_query(query)
        results: list[dict[str, Any]] = []
        for item in documents:
            haystack = self._menu_text(item)
            if not haystack:
                continue

            score = 0.0
            if normalized_query in haystack:
                score += 2.0
            for token in query_tokens:
                if token and token in haystack:
                    score += 0.45

            score += self._lexical_score(query, item)
            if score <= 0:
                continue

            results.append(
                {
                    "id": str(item.get("id") or ""),
                    "brand": item.get("brand"),
                    "name": item.get("name"),
                    "size": item.get("size"),
                    "price": item.get("price"),
                    "description": item.get("description"),
                    "item_type": item.get("item_type"),
                    "drink_category": item.get("drink_category"),
                    "score": score,
                    "dense_score": 0.0,
                    "sparse_score": 0.0,
                    "rrf_score": 0.0,
                }
            )

        results.sort(
            key=lambda item: (
                -float(item.get("score") or 0.0),
                float(item.get("price") or 9999),
                str(item.get("name") or ""),
            )
        )
        return results[:top_k]

    @staticmethod
    def _reciprocal_rank(rank: int, k: int = _DEFAULT_RRF_K) -> float:
        return 1.0 / (k + rank)

    def _merge_hybrid_results(
        self,
        *,
        query: str,
        dense_results: list[dict[str, Any]],
        sparse_results: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        dense_max = max((float(item.get("score") or 0.0) for item in dense_results), default=0.0)
        sparse_max = max((float(item.get("sparse_score") or 0.0) for item in sparse_results), default=0.0)
        merged: dict[str, dict[str, Any]] = {}

        for rank, item in enumerate(dense_results, start=1):
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            merged[item_id] = {
                **item,
                "dense_score": float(item.get("score") or 0.0),
                "sparse_score": 0.0,
                "rrf_score": self._reciprocal_rank(rank),
            }

        for rank, item in enumerate(sparse_results, start=1):
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            current = merged.get(item_id)
            payload = {
                "id": item_id,
                "brand": item.get("brand"),
                "name": item.get("name"),
                "size": item.get("size"),
                "price": item.get("price"),
                "description": item.get("description"),
                "item_type": item.get("item_type"),
                "drink_category": item.get("drink_category"),
            }
            if current is None:
                current = {
                    **payload,
                    "dense_score": 0.0,
                    "sparse_score": float(item.get("sparse_score") or 0.0),
                    "rrf_score": self._reciprocal_rank(rank),
                }
                merged[item_id] = current
            else:
                current["sparse_score"] = max(float(current.get("sparse_score") or 0.0), float(item.get("sparse_score") or 0.0))
                current["rrf_score"] = float(current.get("rrf_score") or 0.0) + self._reciprocal_rank(rank)

        out: list[dict[str, Any]] = []
        normalized_query = self._normalize_query_text(query)
        query_category_terms = [term for term in _MENU_CATEGORY_TERMS if term in normalized_query]
        for item in merged.values():
            dense_norm = float(item.get("dense_score") or 0.0) / dense_max if dense_max > 0 else 0.0
            sparse_norm = float(item.get("sparse_score") or 0.0) / sparse_max if sparse_max > 0 else 0.0
            lexical = self._lexical_score(query, item)
            final_score = (
                dense_norm * 0.42
                + sparse_norm * 0.28
                + float(item.get("rrf_score") or 0.0) * 10.0 * 0.18
                + lexical * 0.22
            )
            item_text = self._normalize_query_text(f"{item.get('name') or ''} {item.get('description') or ''}")
            if query_category_terms and not any(term in item_text for term in query_category_terms):
                final_score -= 0.12
            if self._looks_like_non_drink(item) and any(term in self._normalize_query_text(query) for term in _MENU_CATEGORY_TERMS):
                final_score -= 0.35
            if self._looks_like_packaged_goods(item) and query_category_terms:
                final_score -= 0.2
            out.append(
                {
                    "id": item["id"],
                    "brand": item.get("brand"),
                    "name": item.get("name"),
                    "size": item.get("size"),
                    "price": item.get("price"),
                    "description": item.get("description"),
                    "item_type": item.get("item_type"),
                    "drink_category": item.get("drink_category"),
                    "score": final_score,
                    "dense_score": float(item.get("dense_score") or 0.0),
                    "sparse_score": float(item.get("sparse_score") or 0.0),
                    "rrf_score": float(item.get("rrf_score") or 0.0),
                }
            )

        out.sort(
            key=lambda item: (
                -float(item.get("score") or 0.0),
                float(item.get("price") or 9999),
                str(item.get("name") or ""),
            )
        )
        return out[:top_k]

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
        item_type: str | None = None,
        drink_category: str | None = None,
    ) -> None:
        start = time.perf_counter()
        await self.init_collection()

        models = self._get_models()
        taxonomy = infer_menu_taxonomy({"name": name, "description": description})
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
            "item_type": item_type or taxonomy["item_type"],
            "drink_category": drink_category or taxonomy["drink_category"],
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
        brand = canonicalize_brand_name(brand)
        prepared_query = self._prepare_search_query(query)
        sparse_documents: list[dict[str, Any]] = []
        try:
            settings = get_settings()
            await self.init_collection()

            models = self._get_models()
            query_vector = await self._embedding.embed_text(prepared_query, text_type="query")
            pool_limit = max(top_k * 3, top_k)

            must = [
                models.FieldCondition(key="is_active", match=models.MatchValue(value=True)),
            ]
            if brand:
                must.append(models.FieldCondition(key="brand", match=models.MatchValue(value=brand)))

            client = self._get_client()
            async def _query_points():
                if hasattr(client, "search"):
                    return await client.search(
                        collection_name=self.collection_name,
                        query_vector=query_vector,
                        query_filter=models.Filter(must=must),
                        limit=pool_limit,
                        with_payload=True,
                    )
                result = await client.query_points(
                    collection_name=self.collection_name,
                    query=query_vector,
                    query_filter=models.Filter(must=must),
                    limit=pool_limit,
                    with_payload=True,
                )
                return getattr(result, "points", []) or []

            points = await call_with_resilience(
                f"qdrant.{self.collection_name}",
                _query_points,
                timeout_seconds=settings.qdrant_request_timeout_seconds,
                breaker=get_circuit_breaker(
                    f"qdrant.{self.collection_name}",
                    failure_threshold=settings.dependency_circuit_failure_threshold,
                    recovery_timeout_seconds=settings.dependency_circuit_recovery_seconds,
                ),
            )

            dense_results: list[dict[str, Any]] = []
            for point in points:
                payload = point.payload or {}
                taxonomy = infer_menu_taxonomy(payload)
                dense_results.append(
                    {
                        "id": str(payload.get("id") or point.id),
                        "brand": payload.get("brand"),
                        "name": payload.get("name"),
                        "size": payload.get("size"),
                        "price": payload.get("price"),
                        "description": payload.get("description"),
                        "item_type": payload.get("item_type") or taxonomy["item_type"],
                        "drink_category": payload.get("drink_category") or taxonomy["drink_category"],
                        "score": self._combined_score(query, point),
                    }
                )
            sparse_documents = self._load_sparse_documents(
                brand=brand,
                limit=max(pool_limit * 10, _DEFAULT_SPARSE_SCAN_LIMIT if brand is None else pool_limit * 20),
            )
            sparse_results = self._bm25_sparse_search(query=query, documents=sparse_documents, top_k=pool_limit)
            out = self._merge_hybrid_results(query=query, dense_results=dense_results, sparse_results=sparse_results, top_k=top_k)
            observe_qdrant_search(
                collection=self.collection_name,
                brand_filter=bool(brand),
                outcome="success",
                duration_seconds=time.perf_counter() - start,
            )
            logger.info(
                json.dumps(
                    {
                        "event": "qdrant_search",
                        "collection": self.collection_name,
                        "brand": brand,
                        "top_k": top_k,
                        "query_len": len(query),
                        "prepared_query": prepared_query,
                        "dense_count": len(dense_results),
                        "sparse_count": len(sparse_results),
                        "result_count": len(out),
                        "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
            return out
        except Exception as exc:
            error = exc if isinstance(exc, DependencyError) else DependencyError(
                dependency=f"qdrant.{self.collection_name}",
                category="upstream_error",
                detail=str(exc),
            )
            logger.warning(
                json.dumps(
                    {
                        "event": "qdrant_search_degraded",
                        "collection": self.collection_name,
                        "brand": brand,
                        "query": query,
                        "top_k": top_k,
                        "error": str(error),
                        "error_category": error.category,
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
            if not sparse_documents:
                sparse_documents = self._load_sparse_documents(
                    brand=brand,
                    limit=max(top_k * 20, _DEFAULT_SPARSE_SCAN_LIMIT if brand is None else top_k * 40),
                )

            sparse_results = self._bm25_sparse_search(query=query, documents=sparse_documents, top_k=top_k)
            if sparse_results:
                observe_qdrant_search(
                    collection=self.collection_name,
                    brand_filter=bool(brand),
                    outcome="fallback_sparse",
                    duration_seconds=time.perf_counter() - start,
                )
                logger.info(
                    json.dumps(
                        {
                            "event": "qdrant_search_fallback",
                            "collection": self.collection_name,
                            "brand": brand,
                            "query": query,
                            "fallback_mode": "sparse_only",
                            "degraded_reason": error.category,
                            "result_count": len(sparse_results),
                            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                )
                return sparse_results

            keyword_results = self._keyword_fallback_search(query=query, documents=sparse_documents, top_k=top_k)
            if keyword_results:
                observe_qdrant_search(
                    collection=self.collection_name,
                    brand_filter=bool(brand),
                    outcome="fallback_keyword",
                    duration_seconds=time.perf_counter() - start,
                )
                logger.info(
                    json.dumps(
                        {
                            "event": "qdrant_search_fallback",
                            "collection": self.collection_name,
                            "brand": brand,
                            "query": query,
                            "fallback_mode": "keyword_only",
                            "degraded_reason": error.category,
                            "result_count": len(keyword_results),
                            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                )
                return keyword_results

            observe_qdrant_search(
                collection=self.collection_name,
                brand_filter=bool(brand),
                outcome="degraded_empty",
                duration_seconds=time.perf_counter() - start,
            )
            logger.warning(
                json.dumps(
                    {
                        "event": "qdrant_search_fallback_empty",
                        "collection": self.collection_name,
                        "brand": brand,
                        "query": query,
                        "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
            return []

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
