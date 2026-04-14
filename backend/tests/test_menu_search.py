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


class _RerankQdrantClient(FakeQdrantClient):
    async def search(self, **kwargs):
        self.last_filter = kwargs["query_filter"]
        return [
            SimpleNamespace(
                id="m2",
                score=0.97,
                payload={
                    "id": "m2",
                    "brand": "喜茶",
                    "name": "超级无敌奶盖",
                    "size": "大",
                    "price": 16.0,
                    "description": "通用推荐描述",
                    "is_active": True,
                },
            ),
            SimpleNamespace(
                id="m1",
                score=0.72,
                payload={
                    "id": "m1",
                    "brand": "喜茶",
                    "name": "多肉葡萄",
                    "size": "大",
                    "price": 19.0,
                    "description": "清爽鲜果茶",
                    "is_active": True,
                },
            ),
        ]


def test_search_applies_brand_filter():
    embedding = FakeEmbeddingService()
    models = FakeModels()
    client = FakeQdrantClient()
    service = QdrantService(client=client, models=models, embedding_service=embedding, sparse_document_provider=lambda **kwargs: [])

    results = asyncio.run(service.search(query="葡萄", brand="喜茶", top_k=5))

    assert embedding.last_text == "葡萄"
    assert embedding.last_text_type == "query"

    must_conditions = client.last_filter.must
    keyed = {cond.key: cond.match.value for cond in must_conditions}
    assert keyed["is_active"] is True
    assert keyed["brand"] == "喜茶"

    assert [item["id"] for item in results] == ["m1"]
    assert results[0]["brand"] == "喜茶"
    assert results[0]["name"] == "多肉葡萄"
    assert results[0]["description"] == "清爽葡萄果香"
    assert results[0]["score"] > 0
    assert results[0]["dense_score"] > 0


def test_search_normalizes_brand_alias_before_filtering():
    embedding = FakeEmbeddingService()
    models = FakeModels()
    client = FakeQdrantClient()
    service = QdrantService(client=client, models=models, embedding_service=embedding, sparse_document_provider=lambda **kwargs: [])

    asyncio.run(service.search(query="果茶", brand="一点点", top_k=5))

    must_conditions = client.last_filter.must
    keyed = {cond.key: cond.match.value for cond in must_conditions}
    assert keyed["brand"] == "1点点"


def test_search_reranks_by_lexical_overlap():
    embedding = FakeEmbeddingService()
    models = FakeModels()
    client = _RerankQdrantClient()
    service = QdrantService(client=client, models=models, embedding_service=embedding, sparse_document_provider=lambda **kwargs: [])

    results = asyncio.run(service.search(query="果茶", brand="喜茶", top_k=2))

    assert [item["id"] for item in results] == ["m1", "m2"]
    assert results[0]["score"] > results[1]["score"]


def test_search_sparse_rescues_dense_miss():
    embedding = FakeEmbeddingService()
    models = FakeModels()
    client = FakeQdrantClient()

    async def _empty_search(**kwargs):
        client.last_filter = kwargs["query_filter"]
        return []

    client.search = _empty_search

    service = QdrantService(
        client=client,
        models=models,
        embedding_service=embedding,
        sparse_document_provider=lambda **kwargs: [
            {
                "id": "s1",
                "brand": "喜茶",
                "name": "满杯红柚",
                "size": "大",
                "price": 21.0,
                "description": "经典果茶，红柚果粒丰富",
            }
        ],
    )

    results = asyncio.run(service.search(query="果茶", brand="喜茶", top_k=3))

    assert [item["id"] for item in results] == ["s1"]
    assert results[0]["sparse_score"] > 0
    assert results[0]["dense_score"] == 0.0


def test_search_merges_dense_and_sparse_with_rrf():
    embedding = FakeEmbeddingService()
    models = FakeModels()
    client = _RerankQdrantClient()
    service = QdrantService(
        client=client,
        models=models,
        embedding_service=embedding,
        sparse_document_provider=lambda **kwargs: [
            {
                "id": "m1",
                "brand": "喜茶",
                "name": "多肉葡萄",
                "size": "大",
                "price": 19.0,
                "description": "清爽鲜果茶",
            },
            {
                "id": "s2",
                "brand": "喜茶",
                "name": "芝芝莓莓",
                "size": "大",
                "price": 22.0,
                "description": "热门果茶，草莓风味明显",
            },
        ],
    )

    results = asyncio.run(service.search(query="果茶", brand="喜茶", top_k=3))

    assert [item["id"] for item in results][:2] == ["m1", "s2"]
    assert results[0]["dense_score"] > 0
    assert results[0]["sparse_score"] > 0
    assert results[0]["rrf_score"] > 0


def test_search_degrades_to_sparse_only_when_qdrant_fails():
    embedding = FakeEmbeddingService()
    models = FakeModels()
    client = FakeQdrantClient()

    async def _boom_search(**kwargs):
        raise RuntimeError("qdrant down")

    client.search = _boom_search

    service = QdrantService(
        client=client,
        models=models,
        embedding_service=embedding,
        sparse_document_provider=lambda **kwargs: [
            {
                "id": "s1",
                "brand": "喜茶",
                "name": "满杯红柚",
                "size": "大",
                "price": 21.0,
                "description": "经典果茶，红柚果粒丰富",
            }
        ],
    )

    results = asyncio.run(service.search(query="果茶", brand="喜茶", top_k=3))

    assert [item["id"] for item in results] == ["s1"]
    assert results[0]["sparse_score"] > 0
    assert results[0]["dense_score"] == 0.0


def test_search_degrades_to_keyword_only_when_sparse_has_no_hits():
    embedding = FakeEmbeddingService()
    models = FakeModels()
    client = FakeQdrantClient()

    async def _boom_search(**kwargs):
        raise RuntimeError("qdrant down")

    client.search = _boom_search

    service = QdrantService(
        client=client,
        models=models,
        embedding_service=embedding,
        sparse_document_provider=lambda **kwargs: [
            {
                "id": "k1",
                "brand": "喜茶",
                "name": "葡萄冰茶",
                "size": "大",
                "price": 19.0,
                "description": "",
            }
        ],
    )
    service._bm25_sparse_search = lambda **kwargs: []

    results = asyncio.run(service.search(query="葡萄冰茶", brand="喜茶", top_k=3))

    assert [item["id"] for item in results] == ["k1"]
    assert results[0]["score"] > 0
    assert results[0]["sparse_score"] == 0.0


def test_search_prefers_refreshing_fruit_tea_and_penalizes_snacks():
    embedding = FakeEmbeddingService()
    models = FakeModels()
    client = FakeQdrantClient()

    async def _dense_search(**kwargs):
        client.last_filter = kwargs["query_filter"]
        return [
            SimpleNamespace(
                id="snack-1",
                score=0.95,
                payload={
                    "id": "snack-1",
                    "brand": "蜜雪冰城",
                    "name": "雪王薯薯片-番茄味",
                    "size": "",
                    "price": 2.0,
                    "description": "搭配果茶，开启休憩美妙时光",
                    "is_active": True,
                },
            ),
            SimpleNamespace(
                id="tea-1",
                score=0.81,
                payload={
                    "id": "tea-1",
                    "brand": "喜茶",
                    "name": "清爽芭乐提",
                    "size": "",
                    "price": 19.0,
                    "description": "清爽鲜果茶，果香明显",
                    "is_active": True,
                },
            ),
        ]

    client.search = _dense_search
    service = QdrantService(
        client=client,
        models=models,
        embedding_service=embedding,
        sparse_document_provider=lambda **kwargs: [
            {
                "id": "snack-1",
                "brand": "蜜雪冰城",
                "name": "雪王薯薯片-番茄味",
                "size": "",
                "price": 2.0,
                "description": "搭配果茶，开启休憩美妙时光",
            },
            {
                "id": "tea-1",
                "brand": "喜茶",
                "name": "清爽芭乐提",
                "size": "",
                "price": 19.0,
                "description": "清爽鲜果茶，果香明显",
            },
            {
                "id": "milk-1",
                "brand": "1点点",
                "name": "椰果奶茶",
                "size": "",
                "price": 12.0,
                "description": "清爽不腻，但属于奶茶",
            },
        ],
    )

    results = asyncio.run(service.search(query="清爽的水果茶", brand=None, top_k=3))

    assert results[0]["id"] == "tea-1"
    assert "薯片" not in [item["name"] for item in results[:2]]


def test_search_penalizes_packaged_goods_for_drink_query():
    embedding = FakeEmbeddingService()
    models = FakeModels()
    client = FakeQdrantClient()

    async def _dense_search(**kwargs):
        client.last_filter = kwargs["query_filter"]
        return [
            SimpleNamespace(
                id="pack-1",
                score=0.96,
                payload={
                    "id": "pack-1",
                    "brand": "蜜雪冰城",
                    "name": "酒酿风味花果茶",
                    "size": "",
                    "price": 12.9,
                    "description": "净含量 27.5g/盒，保质期12个月",
                    "is_active": True,
                },
            ),
            SimpleNamespace(
                id="fresh-1",
                score=0.82,
                payload={
                    "id": "fresh-1",
                    "brand": "喜茶",
                    "name": "清爽芭乐提",
                    "size": "",
                    "price": 19.0,
                    "description": "清爽鲜果茶，现制果香明显",
                    "is_active": True,
                },
            ),
        ]

    client.search = _dense_search
    service = QdrantService(
        client=client,
        models=models,
        embedding_service=embedding,
        sparse_document_provider=lambda **kwargs: [
            {
                "id": "pack-1",
                "brand": "蜜雪冰城",
                "name": "酒酿风味花果茶",
                "size": "",
                "price": 12.9,
                "description": "净含量 27.5g/盒，保质期12个月",
            },
            {
                "id": "fresh-1",
                "brand": "喜茶",
                "name": "清爽芭乐提",
                "size": "",
                "price": 19.0,
                "description": "清爽鲜果茶，现制果香明显",
            },
        ],
    )

    results = asyncio.run(service.search(query="清爽的水果茶", brand=None, top_k=2))

    assert results[0]["id"] == "fresh-1"
