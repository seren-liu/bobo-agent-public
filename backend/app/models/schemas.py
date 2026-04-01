from datetime import datetime, date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, ConfigDict, PlainSerializer

# Decimal → float for JSON serialization (JS needs a number, not a string)
JsonDecimal = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float)]


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    nickname: str = Field(default="", max_length=80)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=6, max_length=128)


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class RecordPhotoInput(BaseModel):
    url: str
    sort_order: int = Field(default=0, ge=0)


class RecordPhotoOut(BaseModel):
    url: str
    sort_order: int = Field(default=0, ge=0)
    created_at: datetime | None = None


class RecordInput(BaseModel):
    menu_id: str | None = None
    brand: str
    name: str
    size: str | None = None
    sugar: str | None = None
    ice: str | None = None
    mood: str | None = Field(default=None, max_length=120)
    price: Decimal | None = None
    photo_url: str | None = None
    photos: list[RecordPhotoInput] = Field(default_factory=list, max_length=3)
    source: Literal["manual", "photo", "screenshot", "agent"]
    notes: str | None = None
    consumed_at: datetime


class ConfirmRecordsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[RecordInput] = Field(min_length=1)


class RecordOut(BaseModel):
    id: str
    brand: str
    name: str
    size: str | None = None
    sugar: str | None = None
    ice: str | None = None
    mood: str | None = None
    price: JsonDecimal | None = None
    photo_url: str | None = None
    photos: list[RecordPhotoOut] = Field(default_factory=list)
    source: str
    notes: str | None = None
    consumed_at: datetime
    created_at: datetime


class ConfirmRecordsResponse(BaseModel):
    inserted: int
    records: list[RecordOut]


class CalendarDot(BaseModel):
    brand: str
    color: str


class DayResponse(BaseModel):
    date: date
    records: list[RecordOut]
    photos: list[str]
    total: JsonDecimal


class RecentRecordsResponse(BaseModel):
    records: list[RecordOut]


class BrandDist(BaseModel):
    brand: str
    count: int
    pct: float


class WeekTrend(BaseModel):
    week: str
    count: int


class PrefCount(BaseModel):
    sugar: str | None = None
    ice: str | None = None
    count: int


class StatsResponse(BaseModel):
    total_amount: JsonDecimal
    total_count: int
    brand_dist: list[BrandDist]
    weekly_trend: list[WeekTrend]
    sugar_pref: list[PrefCount]
    ice_pref: list[PrefCount]
    daily_density: dict[str, int] = Field(default_factory=dict)
