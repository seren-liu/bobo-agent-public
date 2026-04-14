from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError

from app.models.schemas import DayResponse, RecentRecordsResponse, StatsResponse


class ToolValidationError(ValueError):
    def __init__(self, *, tool_name: str, phase: str, cause: Exception):
        self.tool_name = tool_name
        self.phase = phase
        self.cause = cause
        super().__init__(f"{tool_name} {phase} validation failed: {cause}")


class _ToolArgsBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: str | None = None
    request_id: str | None = None
    thread_id: str | None = None
    source: str | None = None


class RecordDrinkArgs(_ToolArgsBase):
    brand: str = Field(min_length=1)
    name: str = Field(min_length=1)
    sugar: str | None = None
    ice: str | None = None
    mood: str | None = None
    price: float | None = None
    photo_url: str | None = None
    consumed_at: str | None = None


class SearchMenuArgs(_ToolArgsBase):
    query: str = Field(min_length=1)
    brand: str | None = None


class GetStatsArgs(_ToolArgsBase):
    period: Literal["week", "month", "all"] = "month"
    date: str | None = None


class GetRecentRecordsArgs(_ToolArgsBase):
    limit: int = Field(default=5, ge=1, le=20)


class GetDayArgs(_ToolArgsBase):
    date: date | datetime | str


class GetCalendarArgs(_ToolArgsBase):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)


class UpdateMenuArgs(_ToolArgsBase):
    action: Literal["add", "update", "delete"]
    item: dict[str, Any]


class ToolResultBase(BaseModel):
    model_config = ConfigDict(extra="allow")


class RecordDrinkResult(ToolResultBase):
    ok: bool
    records: list[dict[str, Any]]


class MenuSearchItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    brand: str | None = None
    name: str
    price: float | None = None
    description: str | None = None
    score: float | None = None


class SearchMenuResult(ToolResultBase):
    results: list[MenuSearchItem]
    query: str
    brand: str | None = None


class CalendarDots(RootModel[dict[str, list[dict[str, Any]]]]):
    pass


class UpdateMenuResult(ToolResultBase):
    ok: bool


_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "record_drink": RecordDrinkArgs,
    "search_menu": SearchMenuArgs,
    "get_stats": GetStatsArgs,
    "get_recent_records": GetRecentRecordsArgs,
    "get_day": GetDayArgs,
    "get_calendar": GetCalendarArgs,
    "update_menu": UpdateMenuArgs,
}

_OUTPUT_MODELS: dict[str, Any] = {
    "record_drink": RecordDrinkResult,
    "search_menu": SearchMenuResult,
    "get_stats": StatsResponse,
    "get_recent_records": RecentRecordsResponse,
    "get_day": DayResponse,
    "get_calendar": CalendarDots,
    "update_menu": UpdateMenuResult,
}


def validate_tool_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    model = _INPUT_MODELS.get(tool_name)
    if model is None:
        return dict(args)
    try:
        validated = model.model_validate(args)
    except ValidationError as exc:
        raise ToolValidationError(tool_name=tool_name, phase="input", cause=exc) from exc
    return validated.model_dump(exclude_none=True)


def validate_tool_result(tool_name: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ToolValidationError(tool_name=tool_name, phase="output", cause=TypeError("tool result must be a dict"))
    model = _OUTPUT_MODELS.get(tool_name)
    if model is None:
        return dict(payload)
    try:
        validated = model.model_validate(payload)
    except ValidationError as exc:
        raise ToolValidationError(tool_name=tool_name, phase="output", cause=exc) from exc

    if hasattr(validated, "root"):
        return dict(getattr(validated, "root"))
    return validated.model_dump(exclude_none=True)
