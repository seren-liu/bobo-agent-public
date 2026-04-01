from __future__ import annotations

import json
import logging
from uuid import uuid4
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from app.agent.graph import stream_agent_events
from app.agent.state import audit_agent_event
from app.memory.jobs import enqueue_memory_job, process_memory_jobs
from app.memory import repository

router = APIRouter(prefix="/bobo/agent", tags=["agent"])
logger = logging.getLogger("bobo.agent.api")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    thread_id: str
    user_id: str | None = Field(default=None, description="Deprecated legacy fallback; request.state.user_id takes precedence.")
    max_steps: int = Field(default=10, ge=1, le=30)


def _format_sse(payload: dict[str, Any], *, event: str | None = None, event_id: str | None = None) -> str:
    lines: list[str] = []
    if event:
        lines.append(f"event: {event}")
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False, default=str)}")
    return "\n".join(lines) + "\n\n"


def _extract_text(chunk: Any) -> str:
    content = getattr(chunk, "content", chunk)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    out.append(str(part.get("text", "")))
                elif part.get("type") == "output_text":
                    out.append(str(part.get("text", "")))
            else:
                text = getattr(part, "text", "")
                if text:
                    out.append(str(text))
        return "".join(out)
    return str(content)


def _session_thread_id(user_id: str, session_id: str) -> str:
    clean = session_id.strip()
    if clean.startswith("user-") and ":session-" in clean:
        return clean
    if clean.startswith("session-"):
        clean = clean[len("session-") :]
    return f"user-{user_id}:session-{clean}"


@router.post("/chat")
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    request_user_id = getattr(request.state, "user_id", None)
    if not request_user_id:
        raise HTTPException(status_code=401, detail="missing authenticated user")
    user_id = request_user_id
    request_id = (
        getattr(request.state, "request_id", None)
        or request.headers.get("X-Request-ID")
        or request.headers.get("X-Request-Id")
        or uuid4().hex
    )
    thread_id = _session_thread_id(user_id, payload.thread_id)
    if payload.user_id and payload.user_id != request_user_id:
        logger.warning(
            "chat request ignored body user_id override request_user_id=%s body_user_id=%s request_id=%s",
            request_user_id,
            payload.user_id,
            request_id,
        )
    audit_agent_event(
        "chat",
        stage="start",
        user_id=user_id,
        thread_id=thread_id,
        request_id=request_id,
        message_len=len(payload.message),
    )
    repository.create_thread(user_id, thread_id)
    repository.append_message(
        user_id=user_id,
        thread_key=thread_id,
        role="user",
        content=payload.message,
        request_id=request_id,
        source="agent",
    )

    async def _event_stream():
        assistant_chunks: list[str] = []
        try:
            yield _format_sse(
                {
                    "type": "meta",
                    "request_id": request_id,
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "max_steps": payload.max_steps,
                },
                event="meta",
                event_id=request_id,
            )
            async for event in stream_agent_events(
                message=payload.message,
                user_id=user_id,
                thread_id=thread_id,
                max_steps=payload.max_steps,
                request_id=request_id,
            ):
                event_name = event.get("event")
                if event_name == "on_chat_model_stream":
                    text = _extract_text(event.get("data", {}).get("chunk"))
                    if text:
                        assistant_chunks.append(text)
                        yield _format_sse(
                            {"type": "text", "content": text, "request_id": request_id},
                            event="text",
                            event_id=request_id,
                        )
                elif event_name == "on_tool_start":
                    yield _format_sse(
                        {
                            "type": "tool_call",
                            "tool": event.get("name", ""),
                            "args": event.get("data", {}).get("input", {}),
                            "request_id": request_id,
                        },
                        event="tool_call",
                        event_id=request_id,
                    )
                elif event_name == "on_tool_end":
                    output = event.get("data", {}).get("output")
                    rendered_output = _extract_text(output) or output
                    yield _format_sse(
                        {
                            "type": "tool_result",
                            "tool": event.get("name", ""),
                            "output": rendered_output,
                            "request_id": request_id,
                        },
                        event="tool_result",
                        event_id=request_id,
                    )
                    repository.append_message(
                        user_id=user_id,
                        thread_key=thread_id,
                        role="tool",
                        content=str(rendered_output),
                        request_id=request_id,
                        tool_name=str(event.get("name", "") or ""),
                        tool_call_id=str(event.get("run_id", "") or ""),
                        source="agent",
                    )
            assistant_text = "".join(assistant_chunks).strip()
            if assistant_text:
                repository.append_message(
                    user_id=user_id,
                    thread_key=thread_id,
                    role="assistant",
                    content=assistant_text,
                    request_id=request_id,
                    source="agent",
                )
            enqueue_memory_job(user_id, "thread_summary_refresh", {"thread_key": thread_id}, thread_id)
            enqueue_memory_job(user_id, "memory_extract_from_thread", {"thread_key": thread_id}, thread_id)
            enqueue_memory_job(user_id, "profile_refresh_from_records", {}, thread_id)
            process_memory_jobs(limit=10)
            yield _format_sse({"type": "done", "request_id": request_id}, event="done", event_id=request_id)
            audit_agent_event(
                "chat",
                stage="success",
                user_id=user_id,
                thread_id=thread_id,
                request_id=request_id,
            )
        except RuntimeError as exc:
            if assistant_chunks:
                repository.append_message(
                    user_id=user_id,
                    thread_key=thread_id,
                    role="assistant",
                    content="".join(assistant_chunks).strip(),
                    request_id=request_id,
                    source="agent",
                )
            yield _format_sse(
                {"type": "error", "error": str(exc), "request_id": request_id},
                event="error",
                event_id=request_id,
            )
            yield _format_sse({"type": "done", "request_id": request_id}, event="done", event_id=request_id)
            audit_agent_event(
                "chat",
                stage="error",
                user_id=user_id,
                thread_id=thread_id,
                request_id=request_id,
                error=str(exc),
            )
        except Exception as exc:
            if assistant_chunks:
                repository.append_message(
                    user_id=user_id,
                    thread_key=thread_id,
                    role="assistant",
                    content="".join(assistant_chunks).strip(),
                    request_id=request_id,
                    source="agent",
                )
            yield _format_sse(
                {"type": "error", "error": str(exc), "request_id": request_id},
                event="error",
                event_id=request_id,
            )
            yield _format_sse({"type": "done", "request_id": request_id}, event="done", event_id=request_id)
            audit_agent_event(
                "chat",
                stage="error",
                user_id=user_id,
                thread_id=thread_id,
                request_id=request_id,
                error=str(exc),
            )

    return StreamingResponse(_event_stream(), media_type="text/event-stream")
