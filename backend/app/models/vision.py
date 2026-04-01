from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DrinkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand: str | None = None
    name: str | None = None
    size: str | None = None
    sugar: str | None = None
    ice: str | None = None
    price: float | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class VisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[DrinkItem]
    source_type: Literal["photo", "screenshot"]
    order_time: datetime | None = None
    error: Literal["recognition_failed", "parse_error"] | None = None


class UploadURLRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    filename: str
    content_type: str


class UploadURLResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    upload_url: str
    file_url: str


class RecognizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    image_url: str
    source_type: Literal["photo", "screenshot"]
