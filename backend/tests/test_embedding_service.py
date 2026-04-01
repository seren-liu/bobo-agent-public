from __future__ import annotations

from types import SimpleNamespace

from app.services.embedding import EmbeddingService


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    def __init__(self):
        self.calls: list[dict] = []

    async def post(self, url: str, *, headers: dict, json: dict):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(
            {
                "output": {
                    "embeddings": [
                        {"text_index": 0, "embedding": [0.1, 0.2, 0.3]},
                    ]
                }
            }
        )


class _FailingHttpClient:
    async def post(self, url: str, *, headers: dict, json: dict):
        raise RuntimeError("dashscope unavailable")


class _FakeOpenAIEmbeddings:
    def __init__(self):
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=0, embedding=[0.4, 0.5, 0.6]),
            ]
        )


class _FakeOpenAIClient:
    def __init__(self):
        self.embeddings = _FakeOpenAIEmbeddings()


def test_dashscope_embedding_uses_query_text_type():
    http_client = _FakeHttpClient()
    service = EmbeddingService(
        api_key="dashscope-key",
        model="text-embedding-v4",
        http_client=http_client,
        fallback_api_key=None,
        fallback_model="",
        dimensions=1024,
    )

    vector = __import__("asyncio").run(service.embed_text("清爽的果茶", text_type="query"))

    assert vector == [0.1, 0.2, 0.3]
    assert http_client.calls[0]["json"]["parameters"]["text_type"] == "query"
    assert http_client.calls[0]["json"]["parameters"]["dimension"] == 1024


def test_embedding_falls_back_to_openai_with_same_dimensions():
    fallback_client = _FakeOpenAIClient()
    service = EmbeddingService(
        api_key="dashscope-key",
        model="text-embedding-v4",
        http_client=_FailingHttpClient(),
        fallback_api_key="openai-key",
        fallback_model="text-embedding-3-small",
        fallback_client=fallback_client,
        dimensions=1024,
    )

    vector = __import__("asyncio").run(service.embed_text("少糖水果茶", text_type="query"))

    assert vector == [0.4, 0.5, 0.6]
    assert fallback_client.embeddings.calls[0]["model"] == "text-embedding-3-small"
    assert fallback_client.embeddings.calls[0]["dimensions"] == 1024
    assert fallback_client.embeddings.calls[0]["encoding_format"] == "float"
