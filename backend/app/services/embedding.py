from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("bobo.embedding")


class EmbeddingService:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
        *,
        fallback_api_key: str | None = None,
        fallback_model: str | None = None,
        fallback_client: Any | None = None,
        http_client: Any | None = None,
        dimensions: int | None = None,
        dashscope_base_url: str | None = None,
        openai_base_url: str | None = None,
    ):
        self._model = model or os.getenv("EMBEDDING_MODEL") or "text-embedding-v4"
        self._dimensions = dimensions or self._resolve_dimensions(self._model)
        self._api_key = api_key or self._resolve_api_key(self._model)
        self._client = client
        self._http_client = http_client
        self._dashscope_base_url = (
            dashscope_base_url
            or os.getenv("DASHSCOPE_BASE_URL")
            or "https://dashscope.aliyuncs.com/api/v1"
        ).rstrip("/")
        self._openai_base_url = (openai_base_url or os.getenv("OPENAI_BASE_URL") or "").rstrip("/")

        fallback_model_value = fallback_model or os.getenv("EMBEDDING_FALLBACK_MODEL") or "text-embedding-3-small"
        fallback_key = fallback_api_key or self._resolve_api_key(fallback_model_value)
        self._fallback_model = fallback_model_value if fallback_key and fallback_model_value != self._model else ""
        self._fallback_api_key = fallback_key if self._fallback_model else ""
        self._fallback_client = fallback_client

    @staticmethod
    def _resolve_dimensions(model: str) -> int | None:
        raw = os.getenv("EMBEDDING_DIMENSIONS")
        if raw:
            try:
                return int(raw)
            except ValueError:
                logger.warning("invalid EMBEDDING_DIMENSIONS=%s; fallback to model default", raw)
        if model.startswith("text-embedding-v4") or model.startswith("text-embedding-v3"):
            return 1024
        return None

    @staticmethod
    def _resolve_api_key(model: str) -> str:
        if model.startswith("text-embedding-v"):
            return os.getenv("DASHSCOPE_API_KEY", "") or os.getenv("QWEN_API_KEY", "") or ""
        return os.getenv("OPENAI_API_KEY", "")

    @staticmethod
    def _provider_for_model(model: str) -> str:
        if model.startswith("text-embedding-v"):
            return "dashscope"
        return "openai"

    def vector_size(self) -> int:
        if self._dimensions is not None:
            return self._dimensions
        if self._model == "text-embedding-3-small":
            return 1536
        if self._model == "text-embedding-3-large":
            return 3072
        raise RuntimeError(f"vector size is unknown for embedding model: {self._model}")

    async def embed_text(self, text: str, *, text_type: str = "document") -> list[float]:
        vectors = await self.embed_batch([text], text_type=text_type)
        return vectors[0]

    async def embed_batch(self, texts: list[str], *, text_type: str = "document") -> list[list[float]]:
        if not texts:
            return []

        start = time.perf_counter()
        try:
            out = await self._embed_batch_with_model(texts, model=self._model, text_type=text_type)
            model_used = self._model
            fallback_used = False
        except Exception as exc:
            if not self._fallback_model:
                raise
            logger.warning(
                json.dumps(
                    {
                        "event": "embedding_primary_failed",
                        "model": self._model,
                        "fallback_model": self._fallback_model,
                        "text_type": text_type,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
            out = await self._embed_batch_with_model(texts, model=self._fallback_model, text_type=text_type)
            model_used = self._fallback_model
            fallback_used = True

        logger.info(
            json.dumps(
                {
                    "event": "embedding_batch",
                    "model": model_used,
                    "primary_model": self._model,
                    "fallback_used": fallback_used,
                    "dimensions": self.vector_size(),
                    "text_type": text_type,
                    "batch_count": len(texts),
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return out

    async def _embed_batch_with_model(self, texts: list[str], *, model: str, text_type: str) -> list[list[float]]:
        provider = self._provider_for_model(model)
        if provider == "dashscope":
            return await self._embed_batch_dashscope(texts, model=model, text_type=text_type)
        return await self._embed_batch_openai(texts, model=model)

    async def _embed_batch_dashscope(self, texts: list[str], *, model: str, text_type: str) -> list[list[float]]:
        api_key = self._api_key if model == self._model else self._fallback_api_key
        if not api_key:
            logger.error(json.dumps({"event": "embedding_missing_api_key", "provider": "dashscope"}, ensure_ascii=False))
            raise RuntimeError("DASHSCOPE_API_KEY or QWEN_API_KEY is required for embedding")

        try:
            import httpx
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("httpx package is required for DashScope embedding") from exc

        endpoint = f"{self._dashscope_base_url}/services/embeddings/text-embedding/text-embedding"
        batch_size = 10
        out: list[list[float]] = []

        async def _post(batch: list[str]) -> list[list[float]]:
            payload: dict[str, Any] = {
                "model": model,
                "input": {"texts": batch},
                "parameters": {"output_type": "dense"},
            }
            if self._dimensions is not None:
                payload["parameters"]["dimension"] = self._dimensions
            if text_type:
                payload["parameters"]["text_type"] = text_type

            client = self._http_client
            owns_client = client is None
            if client is None:
                client = httpx.AsyncClient(timeout=60.0)
            try:
                response = await client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
            finally:
                if owns_client:
                    await client.aclose()

            rows = ((body.get("output") or {}).get("embeddings") or [])
            ordered = sorted(rows, key=lambda row: row.get("text_index", row.get("index", 0)))
            return [row["embedding"] for row in ordered]

        for idx in range(0, len(texts), batch_size):
            batch = texts[idx : idx + batch_size]
            out.extend(await _post(batch))
            if idx + batch_size < len(texts):
                await asyncio.sleep(0.2)
        return out

    def _get_openai_client(self, *, model: str) -> Any:
        if model == self._model and self._client is not None:
            return self._client
        if model == self._fallback_model and self._fallback_client is not None:
            return self._fallback_client

        api_key = self._api_key if model == self._model else self._fallback_api_key
        if not api_key:
            logger.error(json.dumps({"event": "embedding_missing_api_key", "provider": "openai"}, ensure_ascii=False))
            raise RuntimeError("OPENAI_API_KEY is required for embedding")

        try:
            from openai import AsyncOpenAI
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("openai package is required for embedding") from exc

        kwargs: dict[str, Any] = {"api_key": api_key}
        if self._openai_base_url:
            kwargs["base_url"] = self._openai_base_url
        client = AsyncOpenAI(**kwargs)
        if model == self._model:
            self._client = client
        elif model == self._fallback_model:
            self._fallback_client = client
        return client

    async def _embed_batch_openai(self, texts: list[str], *, model: str) -> list[list[float]]:
        client = self._get_openai_client(model=model)
        out: list[list[float]] = []
        batch_size = 20

        request_kwargs: dict[str, Any] = {"model": model, "encoding_format": "float"}
        if self._dimensions is not None and model.startswith("text-embedding-3"):
            request_kwargs["dimensions"] = self._dimensions

        for idx in range(0, len(texts), batch_size):
            batch = texts[idx : idx + batch_size]
            response = await client.embeddings.create(input=batch, **request_kwargs)
            sorted_rows = sorted(response.data, key=lambda row: row.index)
            out.extend([row.embedding for row in sorted_rows])

            if idx + batch_size < len(texts):
                await asyncio.sleep(0.5)
        return out
