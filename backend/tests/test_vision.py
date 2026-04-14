from datetime import datetime

import pytest
from pydantic import ValidationError

from app.models.vision import DrinkItem, UploadURLRequest, VisionResult


def test_drink_item_confidence_range():
    item = DrinkItem(name="多肉葡萄", confidence=0.8)
    assert item.confidence == 0.8

    with pytest.raises(ValidationError):
        DrinkItem(name="多肉葡萄", confidence=1.2)


def test_vision_result_source_type_and_error_literal():
    result = VisionResult(
        items=[DrinkItem(name="茉莉奶绿")],
        source_type="photo",
        order_time=datetime(2025, 6, 15, 14, 30, 0),
    )
    assert result.source_type == "photo"

    with pytest.raises(ValidationError):
        VisionResult(items=[], source_type="camera")

    with pytest.raises(ValidationError):
        VisionResult(items=[], source_type="photo", error="unknown_error")


def test_vision_models_forbid_extra_fields():
    with pytest.raises(ValidationError):
        DrinkItem(name="多肉葡萄", extra_key="nope")

    with pytest.raises(ValidationError):
        VisionResult(items=[], source_type="photo", extra_key="nope")


def test_upload_url_request_requires_metadata():
    payload = UploadURLRequest(
        filename="image.jpg",
        content_type="image/jpeg",
        file_size=123456,
        width=1200,
        height=1600,
        source_type="manual",
    )

    assert payload.source_type == "manual"

    with pytest.raises(ValidationError):
        UploadURLRequest(
            filename="image.jpg",
            content_type="image/jpeg",
            file_size=0,
            width=0,
            height=1200,
            source_type="photo",
        )

from app.services.cos import COSService


class _FakeCOSClient:
    def __init__(self):
        self.calls = []

    def get_presigned_url(self, **kwargs):
        self.calls.append(kwargs)
        return "https://upload.example.com/signed?sign=abc"


def test_cos_service_get_upload_url(monkeypatch):
    service = COSService()
    service.bucket = "bobo-1250000000"
    service.region = "ap-shanghai"
    service.scheme = "https"

    fake = _FakeCOSClient()
    monkeypatch.setattr(service, "_create_client", lambda: fake)
    monkeypatch.setattr(service, "_build_key", lambda user_id, filename, content_type: "photos/u-1/2026-03/abc123.jpg")

    result = service.get_upload_url(
        filename="order.jpg",
        content_type="image/jpeg",
        user_id="u-1",
        file_size=300_000,
        width=1280,
        height=720,
        source_type="photo",
    )

    assert result["upload_url"] == "https://upload.example.com/signed?sign=abc"
    assert result["file_url"] == "https://bobo-1250000000.cos.ap-shanghai.myqcloud.com/photos/u-1/2026-03/abc123.jpg"

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["Method"] == "PUT"
    assert call["Bucket"] == "bobo-1250000000"
    assert call["Key"] == "photos/u-1/2026-03/abc123.jpg"
    assert call["Expired"] == 300
    assert call["Params"] == {"ContentType": "image/jpeg"}


def test_cos_service_build_key_uses_expected_prefix_and_extension():
    service = COSService()

    key = service._build_key(user_id="u-1", filename="receipt.png", content_type="image/png")

    assert key.startswith("photos/u-1/")
    assert "/bobo-" in key
    assert key.endswith(".png")


def test_cos_service_rejects_non_image_uploads():
    service = COSService()

    with pytest.raises(ValueError):
        service.get_upload_url(
            filename="notes.txt",
            content_type="text/plain",
            user_id="u-1",
            file_size=10_000,
            width=100,
            height=100,
            source_type="manual",
        )


def test_cos_service_rejects_large_uploads():
    service = COSService()

    with pytest.raises(ValueError, match="image_too_large"):
        service.get_upload_url(
            filename="large.png",
            content_type="image/png",
            user_id="u-1",
            file_size=4 * 1024 * 1024,
            width=1200,
            height=1200,
            source_type="screenshot",
        )

    with pytest.raises(ValueError, match="image_resolution_too_large"):
        service.get_upload_url(
            filename="huge.jpg",
            content_type="image/jpeg",
            user_id="u-1",
            file_size=500_000,
            width=5000,
            height=3000,
            source_type="photo",
        )


def test_cos_service_get_display_url_adds_signature_and_inline(monkeypatch):
    service = COSService()
    service.bucket = "bobo-1250000000"
    service.region = "ap-shanghai"
    service.scheme = "https"
    service.read_url_expired = 1800

    captured: dict[str, object] = {}

    def _fake_presigned(file_url: str, expired: int = 600):
        captured["file_url"] = file_url
        captured["expired"] = expired
        return "https://bobo-1250000000.cos.ap-shanghai.myqcloud.com/photos/u-1/a.jpg?q-sign-algorithm=sha1"

    monkeypatch.setattr(service, "get_presigned_read_url", _fake_presigned)

    result = service.get_display_url("https://bobo-1250000000.cos.ap-shanghai.myqcloud.com/photos/u-1/a.jpg")

    assert captured == {
        "file_url": "https://bobo-1250000000.cos.ap-shanghai.myqcloud.com/photos/u-1/a.jpg",
        "expired": 1800,
    }
    assert "q-sign-algorithm=sha1" in result
    assert "response-content-disposition=inline" in result
from types import SimpleNamespace

from app.services.vision import VisionService


class _FakeVisionClient:
    def __init__(self, content=None, raise_error=False):
        self._content = content
        self._raise_error = raise_error
        self.kwargs = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        if self._raise_error:
            raise TimeoutError("timeout")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


def test_vision_service_recognize_success_and_low_confidence_nullify(monkeypatch):
    content = (
        '{"items":[{"brand":"喜茶","name":"多肉葡萄","size":"大","sugar":"少糖",'
        '"ice":"少冰","price":19.0,"confidence":0.3}],"source_type":"photo",'
        '"order_time":"2025-06-15T14:30:00"}'
    )
    fake = _FakeVisionClient(content=content)
    service = VisionService()
    monkeypatch.setattr(service, "_create_client", lambda: fake)

    result = service.recognize("https://example.com/a.jpg", "screenshot")

    assert result.error is None
    assert result.source_type == "screenshot"
    assert result.order_time is not None
    assert len(result.items) == 1
    assert result.items[0].confidence == 0.3
    assert result.items[0].brand is None
    assert result.items[0].name is None
    assert fake.kwargs["response_format"]["type"] == "json_schema"
    assert fake.kwargs["response_format"]["json_schema"]["strict"] is True


def test_vision_service_recognize_parse_error(monkeypatch):
    fake = _FakeVisionClient(content="not-json")
    service = VisionService()
    monkeypatch.setattr(service, "_create_client", lambda: fake)

    result = service.recognize("https://example.com/a.jpg", "photo")

    assert result.error == "parse_error"
    assert result.items == []
    assert result.degraded is True
    assert result.fallback_mode == "manual_entry"
    assert result.retryable is True
    assert result.message is not None


def test_vision_service_recognize_failed(monkeypatch):
    fake = _FakeVisionClient(raise_error=True)
    service = VisionService()
    monkeypatch.setattr(service, "_create_client", lambda: fake)

    result = service.recognize("https://example.com/a.jpg", "photo")

    assert result.error == "recognition_failed"
    assert result.items == []
    assert result.degraded is True
    assert result.fallback_mode == "manual_entry"
    assert result.retryable is True
    assert result.message is not None
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import vision as vision_api
from app.core.rate_limit import clear_rate_limits
from app.models.vision import VisionResult


def _build_vision_test_client(user_id: str = "u-test") -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def add_user_id(request, call_next):
        request.state.user_id = user_id
        return await call_next(request)

    app.include_router(vision_api.router)
    return TestClient(app)


def setup_function():
    clear_rate_limits()


def test_upload_url_api_uses_request_user_id(monkeypatch):
    client = _build_vision_test_client(user_id="u-abc")

    def _fake_upload(filename: str, content_type: str, user_id: str, **kwargs):
        assert filename == "image.jpg"
        assert content_type == "image/jpeg"
        assert user_id == "u-abc"
        assert kwargs == {
            "file_size": 321000,
            "width": 1200,
            "height": 1600,
            "source_type": "photo",
        }
        return {
            "upload_url": "https://upload.example.com/signed",
            "file_url": "https://cdn.example.com/path/image.jpg",
        }

    monkeypatch.setattr(vision_api._cos_service, "get_upload_url", _fake_upload)

    resp = client.post(
        "/bobo/upload-url",
        json={
            "filename": "image.jpg",
            "content_type": "image/jpeg",
            "file_size": 321000,
            "width": 1200,
            "height": 1600,
            "source_type": "photo",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "upload_url": "https://upload.example.com/signed",
        "file_url": "https://cdn.example.com/path/image.jpg",
    }


def test_upload_url_api_rejects_non_image(monkeypatch):
    client = _build_vision_test_client(user_id="u-abc")

    def _fake_upload(filename: str, content_type: str, user_id: str, **kwargs):
        raise ValueError("unsupported_image_type")

    monkeypatch.setattr(vision_api._cos_service, "get_upload_url", _fake_upload)

    resp = client.post(
        "/bobo/upload-url",
        json={
            "filename": "notes.txt",
            "content_type": "text/plain",
            "file_size": 1200,
            "width": 100,
            "height": 100,
            "source_type": "manual",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unsupported_image_type"


def test_recognize_api_returns_vision_result(monkeypatch):
    client = _build_vision_test_client()

    monkeypatch.setattr(vision_api._cos_service, "validate_user_file_url", lambda image_url, user_id: "photos/u-test/2026-04/test.jpg")

    def _fake_recognize(image_url: str, source_type: str, request_id: str | None = None):
        assert image_url == "https://cdn.example.com/order.jpg"
        assert source_type == "photo"
        assert request_id is None
        return VisionResult(items=[], source_type="photo", order_time=None, error="parse_error")

    monkeypatch.setattr(vision_api._vision_service, "recognize", _fake_recognize)

    resp = client.post(
        "/bobo/vision/recognize",
        json={"image_url": "https://cdn.example.com/order.jpg", "source_type": "photo"},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "items": [],
        "source_type": "photo",
        "order_time": None,
        "error": "parse_error",
        "degraded": False,
        "fallback_mode": None,
        "retryable": None,
        "message": None,
    }


def test_recognize_api_returns_manual_entry_fallback(monkeypatch):
    client = _build_vision_test_client()

    monkeypatch.setattr(vision_api._cos_service, "validate_user_file_url", lambda image_url, user_id: "photos/u-test/2026-04/test.jpg")

    def _fake_recognize(image_url: str, source_type: str, request_id: str | None = None):
        return VisionResult(
            items=[],
            source_type="photo",
            order_time=None,
            error="recognition_failed",
            degraded=True,
            fallback_mode="manual_entry",
            retryable=True,
            message="图片识别暂时失败，建议手动补录。",
        )

    monkeypatch.setattr(vision_api._vision_service, "recognize", _fake_recognize)

    resp = client.post(
        "/bobo/vision/recognize",
        json={"image_url": "https://cdn.example.com/order.jpg", "source_type": "photo"},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "items": [],
        "source_type": "photo",
        "order_time": None,
        "error": "recognition_failed",
        "degraded": True,
        "fallback_mode": "manual_entry",
        "retryable": True,
        "message": "图片识别暂时失败，建议手动补录。",
    }


def test_recognize_api_rejects_external_urls(monkeypatch):
    client = _build_vision_test_client()

    monkeypatch.setattr(vision_api._cos_service, "validate_user_file_url", lambda image_url, user_id: (_ for _ in ()).throw(ValueError("invalid_image_url")))

    resp = client.post(
        "/bobo/vision/recognize",
        json={"image_url": "https://example.com/order.jpg", "source_type": "photo"},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_image_url"


def test_recognize_api_rate_limit(monkeypatch):
    client = _build_vision_test_client()

    calls = {"count": 0}

    def _fake_limit(**kwargs):
        calls["count"] += 1
        if calls["count"] > 1:
            raise vision_api.HTTPException(status_code=429, detail="too many requests")

    monkeypatch.setattr(vision_api, "enforce_rate_limit", _fake_limit)
    monkeypatch.setattr(vision_api._cos_service, "validate_user_file_url", lambda image_url, user_id: "photos/u-test/2026-04/test.jpg")
    monkeypatch.setattr(
        vision_api._vision_service,
        "recognize",
        lambda image_url, source_type, request_id=None: VisionResult(items=[], source_type="photo", order_time=None),
    )

    resp1 = client.post("/bobo/vision/recognize", json={"image_url": "https://cdn.example.com/order.jpg", "source_type": "photo"})
    resp2 = client.post("/bobo/vision/recognize", json={"image_url": "https://cdn.example.com/order.jpg", "source_type": "photo"})

    assert resp1.status_code == 200
    assert resp2.status_code == 429
