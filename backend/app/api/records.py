from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.models.db import delete_record, insert_records, query_calendar, query_day, query_recent, query_stats
from app.models.schemas import (
    ConfirmRecordsRequest,
    ConfirmRecordsResponse,
    DayResponse,
    RecentRecordsResponse,
    StatsResponse,
)
from app.services.cos import COSService

router = APIRouter(prefix="/bobo/records", tags=["records"])
_cos_service = COSService()


def _require_user_id(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="missing authenticated user")
    return str(user_id)


def _to_display_url(url: str | None) -> str | None:
    if not url:
        return url
    try:
        return _cos_service.get_display_url(url)
    except Exception:
        return url


def _decorate_record_photos(record: dict) -> dict:
    output = dict(record)
    photos = []
    for photo in record.get("photos", []) or []:
        photo_output = dict(photo)
        photo_output["url"] = _to_display_url(photo_output.get("url"))
        photos.append(photo_output)
    output["photos"] = photos
    output["photo_url"] = _to_display_url(output.get("photo_url"))
    return output


def _decorate_records(records: list[dict]) -> list[dict]:
    return [_decorate_record_photos(record) for record in records]


@router.post("/confirm", response_model=ConfirmRecordsResponse, status_code=201)
def confirm_records(payload: ConfirmRecordsRequest, request: Request) -> ConfirmRecordsResponse:
    user_id = _require_user_id(request)
    inserted = insert_records(user_id, [item.model_dump() for item in payload.items])
    return ConfirmRecordsResponse(inserted=len(inserted), records=_decorate_records(inserted))


@router.get("/calendar")
def get_calendar(
    request: Request,
    year: int = Query(..., ge=2000),
    month: int = Query(..., ge=1, le=12),
) -> dict:
    return query_calendar(_require_user_id(request), year, month)


@router.get("/day", response_model=DayResponse)
def get_day(request: Request, date: date) -> DayResponse:
    result = query_day(_require_user_id(request), date)
    records = _decorate_records(result["records"])
    photos = [_to_display_url(url) for url in result["photos"]]
    return DayResponse(date=date, records=records, photos=photos, total=result["total"])


@router.get("/recent", response_model=RecentRecordsResponse)
def get_recent(
    request: Request,
    limit: int = Query(5, ge=1, le=20),
) -> RecentRecordsResponse:
    records = _decorate_records(query_recent(_require_user_id(request), limit))
    return RecentRecordsResponse(records=records)


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_record(record_id: str, request: Request) -> Response:
    deleted = delete_record(_require_user_id(request), record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="record not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/stats", response_model=StatsResponse)
def get_stats(
    request: Request,
    period: Literal["week", "month", "all"] = "month",
    date: str | None = None,
) -> StatsResponse:
    return StatsResponse(**query_stats(_require_user_id(request), period, date))
