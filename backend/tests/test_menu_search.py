import asyncio
from types import SimpleNamespace

from app.services.qdrant import QdrantService


class FakeEmbeddingService:
    def __init__(self):
        self.last_text: str | None = None
        self.last_text_type: str | None = None

    def vector_size(self) -> int:
        return 3

    async def embed_text(self, text: str, *, text_type: str = "document") -> list[float]:
        self.last_text = text
        self.last_text_type = text_type
        return [0.1, 0.2, 0.3]


class FakeModels:
    class Distance:
        COSINE = "cosine"

    class PayloadSchemaType:
        KEYWORD = "keyword"
        BOOL = "bool"

    class VectorParams:
        def __init__(self, size: int, distance: str):
            self.size = size
            self.distance = distance

    class MatchValue:
        def __init__(self, value):
            self.value = value

    class FieldCondition:
        def __init__(self, key: str, match):
            self.key = key
            self.match = match

    class Filter:
        def __init__(self, must: list):
            self.must = must

    class PointStruct:
        def __init__(self, id: str, vector: list[float], payload: dict):
            self.id = id
            self.vector = vector
            self.payload = payload

    class PointIdsList:
        def __init__(self, points: list[str]):
            self.points = points


class FakeQdrantClient:
    def __init__(self):
        self.last_filter = None

    async def collection_exists(self, _name: str) -> bool:
        return True

    async def create_collection(self, **_kwargs):
        return None

    async def create_payload_index(self, **_kwargs):
        return None

    async def search(self, **kwargs):
        self.last_filter = kwargs["query_filter"]
        return [
            SimpleNamespace(
                id="m1",
                score=0.92,
                payload={
                    "id": "m1",
                    "brand": "喜茶",
                    "name": "多肉葡萄",
                    "size": "大",
                    "price": 19.0,
                    "description": "清爽葡萄果香",
                    "is_active": True,
                },
            )
        ]

    async def upsert(self, **_kwargs):
        return None

    async def delete(self, **_kwargs):
        return None


def test_search_applies_brand_filter():
    embedding = FakeEmbeddingService()
    models = FakeModels()
    client = FakeQdrantClient()
    service = QdrantService(client=client, models=models, embedding_service=embedding)

    results = asyncio.run(service.search(query="葡萄", brand="喜茶", top_k=5))

    assert embedding.last_text == "葡萄"
    assert embedding.last_text_type == "query"

    must_conditions = client.last_filter.must
    keyed = {cond.key: cond.match.value for cond in must_conditions}
    assert keyed["is_active"] is True
    assert keyed["brand"] == "喜茶"

    assert results == [
        {
            "id": "m1",
            "brand": "喜茶",
            "name": "多肉葡萄",
            "size": "大",
            "price": 19.0,
            "description": "清爽葡萄果香",
            "score": 0.92,
        }
    ]
