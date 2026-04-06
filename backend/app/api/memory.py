from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException, Request

from app.memory.extraction import build_extraction_result, persist_extraction_result
from app.memory.jobs import enqueue_memory_job, process_memory_jobs
from app.memory.models import (
    MemoryItemResponse,
    MemoryProfilePatch,
    MemoryProfileResponse,
    ThreadCreateRequest,
    ThreadMessageResponse,
    ThreadResponse,
    ThreadSummaryResponse,
)
from app.memory.profile import get_profile, patch_profile
from app.memory import repository
from app.memory.summaries import refresh_thread_summary
from app.services.memory_vectors import MemoryVectorService

router = APIRouter(prefix="/bobo/agent", tags=["memory"])


def _request_user_id(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="missing authenticated user")
    return str(user_id)


@router.post("/threads", response_model=ThreadResponse)
def create_thread(payload: ThreadCreateRequest, request: Request) -> ThreadResponse:
    user_id = _request_user_id(request)
    thread_key = payload.thread_key or f"user-{user_id}:session-{uuid4().hex[:12]}"
    row = repository.create_thread(user_id, thread_key, payload.title)
    return ThreadResponse(**row)


@router.get("/threads", response_model=list[ThreadResponse])
def list_threads(request: Request) -> list[ThreadResponse]:
    user_id = _request_user_id(request)
    return [ThreadResponse(**row) for row in repository.list_threads(user_id)]


@router.get("/threads/{thread_id}", response_model=ThreadResponse)
def get_thread(thread_id: str, request: Request) -> ThreadResponse:
    user_id = _request_user_id(request)
    row = repository.get_thread_by_key(user_id, thread_id)
    if not row:
        raise HTTPException(status_code=404, detail="thread not found")
    return ThreadResponse(**row)


@router.get("/threads/{thread_id}/messages", response_model=list[ThreadMessageResponse])
def get_thread_messages(thread_id: str, request: Request) -> list[ThreadMessageResponse]:
    user_id = _request_user_id(request)
    return [ThreadMessageResponse(**row) for row in repository.list_messages(user_id, thread_id)]


@router.get("/threads/{thread_id}/summaries", response_model=list[ThreadSummaryResponse])
def get_thread_summaries(thread_id: str, request: Request) -> list[ThreadSummaryResponse]:
    user_id = _request_user_id(request)
    summary = repository.latest_summary(user_id, thread_id)
    return [ThreadSummaryResponse(**summary)] if summary else []


@router.post("/threads/{thread_id}/archive", response_model=ThreadResponse)
def archive_thread(thread_id: str, request: Request) -> ThreadResponse:
    user_id = _request_user_id(request)
    row = repository.archive_thread(user_id, thread_id)
    if not row:
        raise HTTPException(status_code=404, detail="thread not found")
    return ThreadResponse(**row)


@router.post("/threads/{thread_id}/clear", response_model=ThreadResponse)
def clear_thread(thread_id: str, request: Request) -> ThreadResponse:
    user_id = _request_user_id(request)
    row = repository.clear_thread(user_id, thread_id)
    if not row:
        raise HTTPException(status_code=404, detail="thread not found")
    return ThreadResponse(**row)


@router.get("/profile", response_model=MemoryProfileResponse)
def get_agent_profile(request: Request) -> MemoryProfileResponse:
    user_id = _request_user_id(request)
    return MemoryProfileResponse(**get_profile(user_id))


@router.patch("/profile", response_model=MemoryProfileResponse)
def patch_agent_profile(payload: MemoryProfilePatch, request: Request) -> MemoryProfileResponse:
    user_id = _request_user_id(request)
    row = patch_profile(user_id, payload.model_dump(exclude_none=True))
    return MemoryProfileResponse(**row)


@router.post("/profile/reset", response_model=MemoryProfileResponse)
def reset_agent_profile(request: Request) -> MemoryProfileResponse:
    user_id = _request_user_id(request)
    row = repository.reset_profile(user_id)
    return MemoryProfileResponse(**row)


@router.get("/memories", response_model=list[MemoryItemResponse])
def list_agent_memories(request: Request) -> list[MemoryItemResponse]:
    user_id = _request_user_id(request)
    return [MemoryItemResponse(**row) for row in repository.list_memories(user_id)]


@router.delete("/memories/{memory_id}")
def delete_agent_memory(memory_id: str, request: Request) -> dict[str, bool]:
    user_id = _request_user_id(request)
    if not repository.delete_memory_item(user_id, memory_id):
        raise HTTPException(status_code=404, detail="memory not found")
    MemoryVectorService().delete_memory_item(memory_id)
    return {"ok": True}


@router.post("/memories/{memory_id}/disable")
def disable_agent_memory(memory_id: str, request: Request) -> dict[str, bool]:
    user_id = _request_user_id(request)
    if not repository.disable_memory_item(user_id, memory_id):
        raise HTTPException(status_code=404, detail="memory not found")
    row = next((item for item in repository.list_memories(user_id, include_inactive=True) if str(item["id"]) == memory_id), None)
    if row:
        MemoryVectorService(user_id=user_id).upsert_memory_item(row)
    return {"ok": True}


@router.post("/internal/summaries/rebuild", response_model=list[ThreadSummaryResponse])
def rebuild_summaries(request: Request) -> list[ThreadSummaryResponse]:
    user_id = _request_user_id(request)
    items: list[ThreadSummaryResponse] = []
    for thread in repository.list_threads(user_id):
        row = refresh_thread_summary(user_id, str(thread["thread_key"]), force=True)
        if row:
            items.append(ThreadSummaryResponse(**row))
    return items


@router.post("/internal/profile/refresh", response_model=MemoryProfileResponse)
def refresh_profile(request: Request) -> MemoryProfileResponse:
    user_id = _request_user_id(request)
    enqueue_memory_job(user_id, "profile_refresh_from_records", {})
    process_memory_jobs(limit=10)
    return MemoryProfileResponse(**repository.get_profile(user_id))


@router.post("/internal/memories/extract")
def extract_memories(request: Request) -> dict[str, object]:
    user_id = _request_user_id(request)
    queued_jobs: list[dict[str, object]] = []
    for thread in repository.list_threads(user_id):
        job = enqueue_memory_job(user_id, "memory_extract_from_thread", {"thread_key": str(thread["thread_key"])}, str(thread["thread_key"]))
        queued_jobs.append(dict(job))

    processed_jobs = process_memory_jobs(limit=max(len(queued_jobs), 1))
    completed_jobs = [job for job in processed_jobs if str(job.get("status")) == "completed"]
    failed_jobs = [job for job in processed_jobs if str(job.get("status")) == "failed"]
    return {
        "queued_count": len(queued_jobs),
        "processed_count": len(completed_jobs),
        "failed_count": len(failed_jobs),
        "queued_jobs": queued_jobs,
        "processed_jobs": processed_jobs,
    }


@router.post("/internal/memories/extract-preview")
def extract_memories_preview(
    request: Request,
    payload: dict[str, str | None] = Body(default_factory=dict),
) -> dict[str, object]:
    user_id = _request_user_id(request)
    thread_key = str(payload.get("thread_key") or "").strip()
    if not thread_key:
        threads = repository.list_threads(user_id)
        if not threads:
            return {
                "thread_key": None,
                "thread_message_count": 0,
                "recent_user_message_count": 0,
                "result": None,
                "diagnostics": {"reason": "no_threads"},
            }
        thread_key = str(threads[0]["thread_key"])
    thread = repository.get_thread_by_key(user_id, thread_key)
    result = build_extraction_result(user_id, thread_key)
    diagnostics = dict(result.get("diagnostics") or {})
    return {
        "thread_key": thread_key,
        "thread_message_count": int((thread or {}).get("message_count") or 0),
        "recent_user_message_count": int(diagnostics.get("user_message_count") or 0),
        "result": result,
        "diagnostics": diagnostics,
    }


@router.post("/internal/profile/reconcile")
def reconcile_profile_and_memories(request: Request) -> dict[str, object]:
    user_id = _request_user_id(request)
    results: list[dict[str, object]] = []
    total_memory_upserts = 0
    total_profile_updates = 0

    for thread in repository.list_threads(user_id):
        thread_key = str(thread["thread_key"])
        result = persist_extraction_result(user_id, thread_key)
        diagnostics = dict(result.get("diagnostics") or {})
        total_memory_upserts += int(diagnostics.get("memory_upsert_count") or 0)
        total_profile_updates += int(diagnostics.get("profile_update_count") or 0)
        results.append({"thread_key": thread_key, "diagnostics": diagnostics})

    return {
        "thread_count": len(results),
        "profile_update_count": total_profile_updates,
        "memory_upsert_count": total_memory_upserts,
        "results": results,
    }
