from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request

from app.models.vision import RecognizeRequest, UploadURLRequest, UploadURLResponse, VisionResult
from app.observability import observe_vision_request
from app.core.rate_limit import enforce_rate_limit
from app.services.cos import COSService
from app.services.vision import VisionService

router = APIRouter(tags=["vision"])
logger = logging.getLogger("bobo.vision.api")

_cos_service = COSService()
_vision_service = VisionService()


@router.post("/bobo/upload-url", response_model=UploadURLResponse)
def get_upload_url(payload: UploadURLRequest, request: Request) -> UploadURLResponse:
    request_id = getattr(request.state, "request_id", None)
    user_id = getattr(request.state, "user_id", "") or "anonymous"
    client_ip = getattr(getattr(request, "client", None), "host", "unknown")
    enforce_rate_limit(scope="vision:upload:user", key=f"{user_id}:{client_ip}", max_requests=30, window_seconds=60)
    logger.info(
        json.dumps(
            {
                "event": "vision_upload_url",
                "request_id": request_id,
                "user_id": user_id,
                "filename": payload.filename,
                "content_type": payload.content_type,
                "file_size": payload.file_size,
                "width": payload.width,
                "height": payload.height,
                "source_type": payload.source_type,
            },
            ensure_ascii=False,
            default=str,
        )
    )
    try:
        result = _cos_service.get_upload_url(
            filename=payload.filename,
            content_type=payload.content_type,
            user_id=user_id,
            file_size=payload.file_size,
            width=payload.width,
            height=payload.height,
            source_type=payload.source_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UploadURLResponse(**result)


@router.post("/bobo/vision/recognize", response_model=VisionResult)
def recognize(payload: RecognizeRequest, request: Request) -> VisionResult:
    request_id = getattr(request.state, "request_id", None)
    user_id = getattr(request.state, "user_id", "") or "anonymous"
    client_ip = getattr(getattr(request, "client", None), "host", "unknown")
    enforce_rate_limit(scope="vision:recognize:user", key=f"{user_id}:{client_ip}", max_requests=20, window_seconds=60)
    logger.info(
        json.dumps(
            {
                "event": "vision_recognize_start",
                "request_id": request_id,
                "source_type": payload.source_type,
            },
            ensure_ascii=False,
            default=str,
        )
    )
    try:
        _cos_service.validate_user_file_url(payload.image_url, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Generate a presigned read URL so the vision model can access private COS objects
    readable_url = _cos_service.get_presigned_read_url(payload.image_url)

    result = _vision_service.recognize(
        image_url=readable_url,
        source_type=payload.source_type,
        request_id=request_id,
    )
    observe_vision_request(
        source_type=payload.source_type,
        outcome=result.error or "success",
        item_count=len(result.items),
        low_confidence_count=sum(1 for item in result.items if item.brand is None and item.name is None),
    )
    logger.info(
        json.dumps(
            {
                "event": "vision_recognize_done",
                "request_id": request_id,
                "source_type": payload.source_type,
                "items_count": len(result.items),
                "error": result.error,
            },
            ensure_ascii=False,
            default=str,
        )
    )
    return result
