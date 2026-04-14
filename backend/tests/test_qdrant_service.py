from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID
import pytest

from app.services.qdrant import QdrantService


def test_normalize_point_id_accepts_numeric_and_uuid_and_hashes_strings():
    service = QdrantService(client=object(), models=object())

    assert service._normalize_point_id(123) == 123
    assert service._normalize_point_id("456") == 456
    assert service._normalize_point_id("550e8400-e29b-41d4-a716-446655440000") == "550e8400-e29b-41d4-a716-446655440000"

    hashed = service._normalize_point_id("memory-custom-id")
    assert isinstance(hashed, str)
    assert str(UUID(hashed)) == hashed


def test_upsert_point_uses_normalized_point_id():
    captured: dict[str, object] = {}

    class _FakeClient:
        async def upsert(self, *, collection_name, points, wait):
            captured["collection_name"] = collection_name
            captured["point_id"] = points[0].id
            captured["wait"] = wait

        async def create_payload_index(self, **kwargs):
            return None

    class _FakeModels:
        class PointStruct:
            def __init__(self, id, vector, payload):
                self.id = id
                self.vector = vector
                self.payload = payload

        class PayloadSchemaType:
            KEYWORD = "keyword"

    class _FakeEmbedding:
        def vector_size(self) -> int:
            return 3

        async def embed_text(self, text: str, *, text_type: str = "document"):
            return [0.1, 0.2, 0.3]

    service = QdrantService(
        client=_FakeClient(),
        models=_FakeModels(),
        collection_name="user_memory_vectors",
        embedding_service=_FakeEmbedding(),
    )
    service._initialized = True

    __import__("asyncio").run(
        service.upsert_point(
            point_id="memory-custom-id",
            text="最近预算偏紧",
            payload={"memory_id": "memory-custom-id"},
            keyword_fields=["memory_id"],
        )
    )

    assert captured["collection_name"] == "user_memory_vectors"
    assert isinstance(captured["point_id"], str)
    assert str(UUID(str(captured["point_id"]))) == captured["point_id"]


def test_init_collection_raises_on_vector_size_mismatch():
    class _FakeClient:
        async def collection_exists(self, _name):
            return True

        async def get_collection(self, _name):
            return SimpleNamespace(
                config=SimpleNamespace(
                    params=SimpleNamespace(
                        vectors=SimpleNamespace(size=1536),
                    )
                )
            )

    class _FakeModels:
        class PayloadSchemaType:
            KEYWORD = "keyword"
            BOOL = "bool"

    class _FakeEmbedding:
        def vector_size(self) -> int:
            return 1024

    service = QdrantService(
        client=_FakeClient(),
        models=_FakeModels(),
        embedding_service=_FakeEmbedding(),
    )

    with pytest.raises(RuntimeError, match="vector size mismatch"):
        __import__("asyncio").run(service.init_collection())


def test_search_falls_back_to_sparse_when_embedding_unavailable():
    class _FailingEmbedding:
        async def embed_text(self, text: str, *, text_type: str = "document"):
            raise RuntimeError("embedding timeout")

        def vector_size(self) -> int:
            return 3

    service = QdrantService(
        client=object(),
        models=object(),
        embedding_service=_FailingEmbedding(),
        sparse_document_provider=lambda **_kwargs: [
            {"id": "m1", "brand": "喜茶", "name": "多肉葡萄", "description": "葡萄果茶", "price": 19, "item_type": "drink", "drink_category": "fruit_tea"}
        ],
    )

    results = __import__("asyncio").run(service.search(query="葡萄", brand="喜茶", top_k=3))

    assert results
    assert results[0]["name"] == "多肉葡萄"
