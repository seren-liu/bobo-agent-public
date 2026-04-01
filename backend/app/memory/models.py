from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, ConfigDict


class ThreadCreateRequest(BaseModel):
    thread_key: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=120)


class ThreadResponse(BaseModel):
    id: str
    user_id: str
    thread_key: str
    title: str | None = None
    status: str
    message_count: int
    last_user_message_at: datetime | None = None
    last_agent_message_at: datetime | None = None
    last_summary_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class ThreadMessageResponse(BaseModel):
    id: str
    thread_id: str
    user_id: str
    role: str
    content: str
    content_type: str = "text"
    request_id: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    source: str = "agent"
    created_at: datetime


class ThreadSummaryResponse(BaseModel):
    id: str
    thread_id: str
    user_id: str
    summary_type: str
    summary_text: str
    open_slots: list[Any] = Field(default_factory=list)
    covered_message_count: int = 0
    token_estimate: int | None = None
    created_at: datetime


class MemoryProfilePatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_preferences: dict[str, Any] | None = None
    drink_preferences: dict[str, Any] | None = None
    interaction_preferences: dict[str, Any] | None = None
    budget_preferences: dict[str, Any] | None = None
    health_preferences: dict[str, Any] | None = None


class MemoryProfileResponse(BaseModel):
    user_id: str
    profile_version: int = 1
    display_preferences: dict[str, Any] = Field(default_factory=dict)
    drink_preferences: dict[str, Any] = Field(default_factory=dict)
    interaction_preferences: dict[str, Any] = Field(default_factory=dict)
    budget_preferences: dict[str, Any] = Field(default_factory=dict)
    health_preferences: dict[str, Any] = Field(default_factory=dict)
    memory_updated_at: datetime
    created_at: datetime
    updated_at: datetime


class MemoryItemResponse(BaseModel):
    id: str
    user_id: str
    memory_type: str
    scope: str
    content: str
    normalized_fact: dict[str, Any] | None = None
    source_kind: str
    source_ref: str | None = None
    confidence: float
    salience: float
    status: str
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None


class MemoryJobResponse(BaseModel):
    id: str
    user_id: str
    thread_id: str | None = None
    job_type: str
    payload: dict[str, Any]
    status: str
    attempt_count: int
    last_error: str | None = None
    scheduled_at: datetime
    created_at: datetime
    updated_at: datetime

